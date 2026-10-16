import wx
import json
# import wx.gizmos as gizmos
import wx.dataview as dv
from src.core.path_manager import PathManager
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.media.m3u8.ffmpeg_converter import FFmpegConverter
from pathlib import Path
from src.models.presentation import TaskSummary, single_file_info
from src.schemas.task import FileStatus, TaskStatus, TaskType, M3U8Task
from src.models.file_base import FileItem, TreeData, TreeItem


def _file(path, display_name, source_url='', *, size=None, status=FileStatus.PENDING):
    """只投影任务记录；展示层不查询磁盘文件。"""
    return FileItem(fileName=str(path), displayName=display_name, absUri=source_url or '',
                    status=status, fileSize=size if size is not None else (
                        '—' if status == FileStatus.COMPLETED else '-'), modifyAt='—')


def to_tree_item(task, previous=None):
    """数据库记录决定文件行及状态，未知的文件信息显示为空缺。"""
    name = Path(task.name).stem
    if not isinstance(task, M3U8Task):
        target = task.save_dir / task.details.target_path
        parent = _file(target, name, task.source_url, size=task.progress.downloaded_bytes)
        parent.modifyAt = task.updated_at.astimezone().strftime('%Y-%m-%d %H:%M')
        outputs = []
        if task.status == TaskStatus.COMPLETED:
            outputs = [_file(target, target.name, size=task.progress.downloaded_bytes,
                             status=FileStatus.COMPLETED)]
        elif task.type == TaskType.MP4 and not task.details.file_missing:
            partial = task.save_dir / task.details.temporary_path
            outputs = [_file(partial, partial.name, size=task.progress.downloaded_bytes)]
        return TreeItem(task_id=task.id, task_type=task.type, save_dir=task.save_dir,
                        progress=task.progress, task_status=task.status,
                        last_error=task.last_error, parent=parent, outputs=outputs)
    playlist = task.save_dir / (task.details.playlist_path or 'download.m3u8')
    size = task.progress.downloaded_bytes
    if task.details.work_dir_missing:
        size = next((o.size_bytes for o in task.outputs if o.status == FileStatus.COMPLETED), size)
    parent = _file(playlist, name, task.source_url, size=size)
    parent.modifyAt = task.updated_at.astimezone().strftime('%Y-%m-%d %H:%M')
    children = []
    previous_children = {child.sequence: child for child in previous.childs} if previous else {}
    for segment in sorted(task.details.segments, key=lambda segment: segment.sequence):
        path = task.save_dir / segment.relative_path
        child = previous_children.get(segment.sequence)
        if (child is None or child.status != segment.status or child.fileName != str(path)
                or child.absUri != (segment.source_url or '')):
            child = _file(path, path.name, segment.source_url, size=(
                segment.size_bytes if segment.status == FileStatus.COMPLETED else None), status=segment.status)
        child.sequence = segment.sequence
        children.append(child)
    outputs = [_file(task.save_dir / output.relative_path, Path(output.relative_path).name,
                     size=output.size_bytes, status=output.status)
               for output in task.outputs if output.status == FileStatus.COMPLETED]
    return TreeItem(task_id=task.id, task_type=task.type, save_dir=task.save_dir,
                    manifests={-2: _file(playlist, playlist.name)},
                    work_dir=playlist.parent,
                    work_dir_missing=task.details.work_dir_missing,
                    output_path=task.save_dir / next(o.relative_path for o in task.outputs if o.kind == 'merged'),
                    duration_pending=task.details.detect_duration and any(
                        s.status == FileStatus.COMPLETED and s.duration_status == 'pending'
                        for s in task.details.segments),
                    progress=task.progress, task_status=task.status, last_error=task.last_error,
                    parent=parent, childs=children, outputs=outputs,
                    download=task.progress.completed_segments)


def load_tree(service):
    """读取恢复后的任务记录，再转换成列表行；核心服务不依赖界面结构。"""
    return TreeData(items=[to_tree_item(task) for task in service.load_tasks()])



# 定义自定义事件类型
ALL_DOWNLOAD_EVENT = wx.NewEventType()
# 创建事件绑定器
EVT_ALL_DOWNLOAD = wx.PyEventBinder(ALL_DOWNLOAD_EVENT, 1)
class AllDownloadEvent(wx.PyCommandEvent):
    """自定义事件类"""
    def __init__(self, event_type, id):
        super().__init__(event_type, id)
        self.data = None
    
    def SetData(self, data):
        self.data = data
    
    def GetData(self):
        return self.data

class MultiColumnTreeModel(dv.PyDataViewModel):
    """把文件任务数据转换为 wx.DataViewCtrl 可读取的树形模型。

    AssociateModel 绑定后，wx 自动查询 GetChildren/GetParent 等方法构建树，
    再通过 GetValue 读取单元格内容，交给对应列的渲染器显示。
    本类不绘制按钮，也不执行下载；它提供数据、状态和操作是否可用的信息。

    节点键使用零基索引：'0' 表示第一个任务，'0.1' 表示该任务的第二个子节点。
    非负文件索引先 outputs，再 childs；分片的父节点由 GetParent 指向 segments。
    -4 是工作目录，-1 是 segments，-2 是播放列表；不占用分片索引。
    刷新后任务顺序可能改变，异步回调按任务 UUID 和分片序号定位。
    """
    def __init__(self, tree: TreeData, parent=None):
        super().__init__()
        # 首次读取和恢复由启动入口完成；构造模型仅接收展示数据，不访问任务库。
        self.fileTree = tree
        # None 表示全部；筛选只改变根节点可见性，不删任务、不改变下载回调使用的索引。
        self.visible_tasks = None
        # 因为 ObjectToItem(obj) 在库内部维护一张map，key 为 id(obj)，所以 obj 对象不能变
        # id(obj) 函数返回对象的"标识值"
        self.keyMap = dict()

        self.parent = parent
        self.merge_failed = set()
        self.row_numbers = {}
        self.UpdateRowNumbers()

    def UpdateRowNumbers(self, tree=None):
        """按展开顺序缓存可见行序号；不改变节点身份，也不访问磁盘。"""
        numbers = {}

        def visit(parent):
            children = []
            self.GetChildren(parent, children)
            for item in children:
                key = self.ItemToObject(item)
                numbers[key] = str(len(numbers) + 1)
                if tree is not None and self.IsContainer(item) and tree.IsExpanded(item):
                    visit(item)

        visit(dv.NullDataViewItem)
        changed = numbers != self.row_numbers
        self.row_numbers = numbers
        return changed

    def ApplyTaskRecord(self, record):
        """只替换匹配 UUID 的行数据，回调不依赖刷新前的行号；不在此处整表重绘。"""
        if record is None:
            return None
        for index, task in enumerate(self.fileTree.items):
            if task.task_id == record.id:
                self.fileTree.items[index] = to_tree_item(record, previous=task)
                return index
        return None

    def TaskInfo(self, index) -> TaskSummary:
        """自定义方法：汇总指定任务的分片进度和运行状态，供界面和 GetValue 使用。

        index 是 fileTree.items 的索引；M3U8 按分片数、MP4 按字节、RTMP 按时长显示。
        此方法只查询状态，不启动下载或合并。
        """
        task = self.fileTree.items[index]
        if task.task_type != TaskType.M3U8:
            return single_file_info(task)
        total = len(task.childs)
        done = sum(child.fileSize != '-' for child in task.childs)
        keys = {M3U8Downloader.FileKey(PathManager.GetAbsPath(child.fileName))
                for child in task.childs if child.fileSize == '-'}
        snapshot = M3U8Downloader.Snapshot()
        # 下载器管理全部任务；集合交集只取出属于当前任务的未完成文件。
        # pending 包含排队/处理中任务，requesting 只包含尚未结束的网络请求。
        pending = keys & snapshot['pending']
        requesting = keys & snapshot['requesting']
        stored_failed = {M3U8Downloader.FileKey(child.fileName) for child in task.childs
                         if child.status == FileStatus.FAILED and child.fileSize == '-'}
        failed = ((keys & snapshot['failed']) | stored_failed) - snapshot['pending']
        paused = snapshot['paused'] or bool(pending and pending <= snapshot['paused_files'])
        merging = task.task_status == TaskStatus.MERGING or bool(
            task.output_path and FFmpegConverter.IsConverting(str(task.output_path)))
        runner = getattr(self.parent, 'runner', None)
        if not merging and runner is not None and runner.busy_processing(task.task_id):
            status, merging = '检测时长', True
        elif task.duration_pending and total and done == total:
            merging = bool(runner is not None and runner.busy_processing(task.task_id))
            status = '检测时长' if merging else '待检测时长'
        elif merging:
            status = '合并中'
        elif task.parent.fileName in self.merge_failed or (
                task.task_status == TaskStatus.FAILED and total and done == total and not task.outputs):
            status = '合并失败'
        elif task.outputs:
            status = '已完成'
        elif task.task_status == TaskStatus.INTERRUPTED and not pending:
            status = '已中断'
        elif total and done == total:
            status = '待合并'
        elif pending:
            status = ('暂停中' if requesting else '已暂停') if paused else (
                '下载中' if requesting else '等待下载')
        elif task.task_status in (TaskStatus.PAUSED, TaskStatus.PAUSING):
            status = '已暂停'
        elif failed:
            status = '下载失败'
        elif task.task_status == TaskStatus.PARTIAL:
            status = '部分完成'
        else:
            status = '未开始' if not done else '待继续'
        if failed:
            status += f' · {len(failed)} 个失败'
        output_only = task.work_dir_missing and bool(task.outputs)
        return {
            'total': total,
            'done': done,
            'percent': 100 if output_only else (int(done * 100 / total) if total else 0),
            'status': status,
            'failed': failed,
            'pending': pending,
            'merging': merging,
            'paused': paused,
            'progress': '100%' if output_only else f'{int(done * 100 / total) if total else 0}% · {done}/{total}',
        }


    def ChildStatus(self, index, child_index, snapshot=None):
        """文件子行只展示自身状态；MP4 临时文件与任务共享状态。"""
        task = self.fileTree.items[index]
        if child_index < 0:
            return ''
        if task.task_type == TaskType.MP4:
            return self.TaskInfo(index)['status']
        if child_index < len(task.outputs):
            return '已完成'
        child = task.childs[child_index - len(task.outputs)]
        if child.fileSize != '-' or child.status == FileStatus.COMPLETED:
            return '已完成'
        snapshot = M3U8Downloader.Snapshot() if snapshot is None else snapshot
        key = M3U8Downloader.FileKey(PathManager.GetAbsPath(child.fileName))
        paused = snapshot['paused'] or key in snapshot['paused_files']
        if key in snapshot['requesting']:
            return '暂停中' if paused else '下载中'
        if key in snapshot['pending']:
            return '已暂停' if paused else '等待下载'
        if key in snapshot['failed'] or child.status == FileStatus.FAILED:
            return '下载失败'
        if task.task_status in (TaskStatus.PAUSED, TaskStatus.PAUSING):
            return '已暂停'
        if task.task_status == TaskStatus.INTERRUPTED:
            return '已中断'
        return '文件缺失' if child.status == FileStatus.MISSING else '未开始'

    def CanDownloadSegment(self, index, key):
        """操作列、右键菜单和点击入口共用实时判断；key 包含输出文件偏移。"""
        task = self.fileTree.items[index]
        segment_index = key - len(task.outputs)
        if task.task_type != TaskType.M3U8 or task.work_dir_missing or not 0 <= segment_index < len(task.childs):
            return False
        child = task.childs[segment_index]
        if child.fileSize != '-' or child.status == FileStatus.COMPLETED:
            return False
        # 这里只需判断是否在处理媒体，不能调用 TaskInfo 遍历整个任务的分片。
        if task.task_status == TaskStatus.MERGING or (task.output_path and
                FFmpegConverter.IsConverting(str(task.output_path))):
            return False
        runner = getattr(self.parent, 'runner', None)
        if runner is not None and runner.busy_processing(task.task_id):
            return False
        return M3U8Downloader.CanStartFile(PathManager.GetAbsPath(child.fileName))

    def FileActions(self, index, key):
        """工作目录和 MP4 文件的行内删除；不在绘制阶段访问磁盘。"""
        task = self.fileTree.items[index]
        if task.task_type == TaskType.M3U8 and key == -4:
            if task.work_dir_missing:
                return []
            info = self.TaskInfo(index)
            snapshot = M3U8Downloader.Snapshot()
            requesting = any(M3U8Downloader.FileKey(PathManager.GetAbsPath(child.fileName)) in snapshot['requesting']
                             for child in task.childs)
            enabled = not info['merging'] and (not info['pending'] or (info['paused'] and not requesting))
            return [dict(id='delete_work', label='删除', enabled=bool(enabled))]
        if 0 <= key < len(task.outputs) and task.task_type in (TaskType.M3U8, TaskType.MP4):
            if task.task_type == TaskType.M3U8:
                active = task.task_status == TaskStatus.MERGING or bool(
                    task.output_path and FFmpegConverter.IsConverting(str(task.output_path)))
            else:
                active = task.task_status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING)
            return [dict(id='delete_output', label='删除', enabled=not active)]
        return []

    def TaskActions(self, index):
        """自定义方法：按任务类型返回操作的 id、显示文字和可用状态。

        GetValue 将这些数据序列化后交给 TaskActionRenderer；渲染器根据 enabled
        置灰，根据 id 分发点击，不通过中文显示文字判断要执行什么操作。
        """
        task = self.fileTree.items[index]
        if task.task_type == TaskType.MP4:
            active = task.task_status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING, TaskStatus.PAUSING)
            finished = task.task_status == TaskStatus.COMPLETED
            label = '暂停' if active else ('继续' if task.progress.downloaded_bytes or
                task.task_status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED) else '开始')
            return [
                dict(id='start', label=label, enabled=not finished and task.task_status != TaskStatus.PAUSING),
                dict(id='retry', label='重试', enabled=task.task_status == TaskStatus.FAILED),
                dict(id='delete', label='删除', enabled=not active),
                dict(id='more', label='更多', enabled=True),
            ]
        if task.task_type != TaskType.M3U8:
            # 引擎在后续步骤接入；此处提供稳定布局，禁用尚不可执行的操作。
            live = task.task_type == TaskType.RTMP
            active = task.task_status in (TaskStatus.QUEUED, TaskStatus.DOWNLOADING,
                TaskStatus.PAUSING, TaskStatus.RECORDING, TaskStatus.STOPPING)
            return [
                dict(id='start', label='录制' if live else '开始', enabled=False),
                dict(id='stop' if live else 'retry', label='停止' if live else '重试', enabled=False),
                dict(id='delete', label='删除', enabled=not active),
                dict(id='more', label='更多', enabled=True),
            ]
        info = self.TaskInfo(index)
        idle = not info['pending'] and not info['merging']
        # 主动开始/重试由 TaskRunner 恢复当前任务，不受其他任务暂停状态阻挡。
        downloadable = idle and not task.outputs

        # 第一项在排队/下载期间可暂停；暂停后恢复当前任务，不重新提交分片。
        running = bool(info['pending']) and not info['merging']
        label = ('继续' if info['paused'] else '暂停') if running else (
            '继续' if info['done'] or task.task_status in (TaskStatus.PAUSED, TaskStatus.INTERRUPTED) else '开始')
        return [
            dict(id='start', label=label,
                 enabled=bool(running or (downloadable and (info['done'] < info['total'] or task.duration_pending)))),
            dict(id='retry', label='重试', enabled=bool(downloadable and info['failed'])),
            dict(id='delete', label='删除', enabled=idle),
            dict(id='more', label='更多', enabled=True),
        ]

    def _SendEvent(self, payload=None):
        """自定义方法：分片全部完成时，将通知投递到父窗口的事件队列。

        主窗口通过 Bind(EVT_ALL_DOWNLOAD, ...) 接收；PostEvent 不会在这里同步
        执行窗口处理函数。返回 True 表示已投递，不代表后续 MP4 合并成功。
        """
        if not self.parent:
            return False
        
        event = AllDownloadEvent(ALL_DOWNLOAD_EVENT, -1)
        event.SetData(payload)
        
        # # 不可用，报错
        # # 获取事件处理器并处理事件
        # # 发送事件
        # owner = self.GetOwner()
        # if owner and hasattr(owner, 'GetEventHandler'):
        #     owner.GetEventHandler().ProcessEvent(event)
        #     return True
        # return False

        # 直接发送到父窗口
        wx.PostEvent(self.parent, event)
        return True

    # 自定义函数
    def _BuildKey(self, keys: tuple):
        """自定义方法：把索引元组转为节点键，并缓存同一个字符串对象。

        ObjectToItem 依赖对象身份；同一节点反复查询时需要复用缓存中的对象。
        """
        _key = ".".join(map(str, keys))
        # 维护 obj 内存地址不变
        if _key not in self.keyMap.keys():
            self.keyMap[_key] = _key
        return self.keyMap[_key]
    
    # 自定义函数
    def ParseKey(self, keyStr: str):
        """自定义方法：例如将 '2.3' 还原为 (2, 3)，用于定位任务和子节点。"""
        return tuple(map(int, keyStr.split(".")))

    def GetItemToolTip(self, item):
        """从现有任务快照生成提示，不查询磁盘或网络。"""
        if not item.IsOk():
            return ''
        keys = self.ParseKey(self.ItemToObject(item))
        task = self.fileTree.items[keys[0]]
        if len(keys) == 1:
            lines = ['名称：' + (task.parent.displayName or Path(task.parent.fileName).name)]
            if task.parent.absUri:
                lines.append('来源地址：' + task.parent.absUri)
            lines.append('保存目录：' + str(task.save_dir or Path(task.parent.fileName).parent))
            return '\n'.join(lines)
        key = keys[1]
        if key in (-4, -1):
            directory = task.work_dir or Path(task.parent.fileName).parent
            if key == -1:
                directory /= 'segments'
            return '目录：' + str(directory)
        if key in task.manifests:
            file = task.manifests[key]
            source_url = ''
        elif 0 <= key < len(task.outputs) + len(task.childs):
            if key < len(task.outputs):
                file = task.outputs[key]
                source_url = task.parent.absUri if task.task_type == TaskType.MP4 else ''
            else:
                file = task.childs[key - len(task.outputs)]
                source_url = file.absUri
        else:
            return ''
        lines = ['文件名：' + (file.displayName or Path(file.fileName).name), '本地路径：' + file.fileName]
        if source_url:
            lines.append('下载地址：' + source_url)
        return '\n'.join(lines)

    def GetColumnCount(self):
        """重写：wx 查询模型列数，模型列编号与界面显示顺序不一定相同。

        0 序列、1 文件名、2 文件大小、3 修改时间、4 操作、5 下载进度、6 状态。
        界面通过 DataViewColumn 的 model_column 参数绑定这些编号。
        """
        return 7  # 原有列、进度、状态
    
    def GetColumnType(self, col):
        """重写：wx 查询指定模型列的数据类型；此处所有列都按 string 传递。

        操作列虽然包含多个操作，也以 JSON 字符串传给自定义渲染器。
        """
        return "string"
    
    # 父类函数
    def IsContainer(self, item):
        """重写：M3U8 和 MP4 的文件可展开；RTMP 保持单行。"""
        if not item.IsOk():
            return True
        
        keys = self.ItemToObject(item)
        objs = self.ParseKey(keys)
        # print("IsContainer", keys)
        if len(objs) == 1:
            task = self.fileTree.items[objs[0]]
            return task.task_type in (TaskType.M3U8, TaskType.MP4) and bool(
                task.outputs or (not task.work_dir_missing and (task.childs or task.manifests)))
        elif len(objs) == 2:
            task = self.fileTree.items[objs[0]]
            return not task.work_dir_missing and (
                (objs[1] == -1 and bool(task.childs)) or (objs[1] == -4 and bool(task.manifests or task.childs)))
        else:
            pass
        return False

    # 父类函数
    def HasContainerColumns(self, item):
        """重写：返回 True，让任务行也能显示文件名以外的进度、状态等列。"""
        return True

    # 父类函数
    def GetChildren(self, parent, children):
        """重写：wx 查询子节点时调用，将 DataViewItem 追加到 children 并返回数量。

        无效 parent 表示不可见的虚拟根，此时返回符合筛选条件的任务；任务下面则返回
        输出、清单和 segments；分片只在 segments 下显示。
        """
        if not parent.IsOk():  # 根节点
            for idx, mu in enumerate(self.fileTree.items):
                if self.visible_tasks is not None and idx not in self.visible_tasks:
                    continue
                _key = self._BuildKey((idx,))
                children.append(self.ObjectToItem(_key))
            return len(children)

        keys = self.ItemToObject(parent)
        objs = self.ParseKey(keys)
        # print("GetChildren parent:", keys)
        if len(objs) == 1:
            idxi = objs[0]
            if self.fileTree.items[idxi].task_type not in (TaskType.M3U8, TaskType.MP4):
                return 0
            # 处理 MP4　文件
            idxj = 0
            for _ in self.fileTree.items[idxi].outputs:
                _key = self._BuildKey((idxi, idxj))
                children.append(self.ObjectToItem(_key))
                idxj += 1

            task = self.fileTree.items[idxi]
            if task.task_type == TaskType.M3U8 and not task.work_dir_missing and (task.manifests or task.childs):
                children.append(self.ObjectToItem(self._BuildKey((idxi, -4))))
            return len(children)
        elif len(objs) == 2 and objs[1] == -4:
            idxi = objs[0]
            task = self.fileTree.items[idxi]
            if task.work_dir_missing:
                return 0
            for key in task.manifests:
                children.append(self.ObjectToItem(self._BuildKey((idxi, key))))
            if task.childs:
                children.append(self.ObjectToItem(self._BuildKey((idxi, -1))))
            return len(children)
        elif len(objs) == 2 and objs[1] == -1:
            idxi = objs[0]
            task = self.fileTree.items[idxi]
            if task.work_dir_missing:
                return 0
            for offset in range(len(task.childs)):
                _key = self._BuildKey((idxi, len(task.outputs) + offset))
                children.append(self.ObjectToItem(_key))
            return len(children)
        return 0
    
    def ItemChanged(self, item):
        """隐藏任务仍可更新数据，但不用通知视图重绘不存在的行。"""
        if item.IsOk() and self.visible_tasks is not None:
            index = self.ParseKey(self.ItemToObject(item))[0]
            if index not in self.visible_tasks:
                return True
        return super().ItemChanged(item)

    def ValueChanged(self, item, col):
        """筛选隐藏的行不发送单元格重绘通知，数据仍保留在完整任务列表中。"""
        if item.IsOk() and self.visible_tasks is not None:
            index = self.ParseKey(self.ItemToObject(item))[0]
            if index not in self.visible_tasks:
                return True
        return super().ValueChanged(item, col)

    @staticmethod
    def _DisplayMetadata(value):
        # 只隐藏显示占位符；fileSize 的 '-' 仍用于内部未完成判断。
        return '' if value in ('-', '—', '----:--:-- --:--') else value

    # 父类函数
    def GetValue(self, item, col):
        """重写：wx 准备显示单元格时，按节点 item 和模型列 col 读取数据。

        操作列的数据链是：本方法 → 渲染器 SetValue → 渲染器 Render。
        GetValue 应只提供内容，不在这里下载文件或处理点击；空字符串表示无内容。
        """
        keys = self.ItemToObject(item)
        objs = self.ParseKey(keys)
        # print("GetValue keys:", keys)
        if col == 0:
            return self.row_numbers.get(keys, '')
        if len(objs) == 2 and objs[1] < 0:
            if col == 4:
                actions = self.FileActions(objs[0], objs[1])
                return json.dumps(actions, ensure_ascii=False) if actions else ''
            if objs[1] == -4:
                task = self.fileTree.items[objs[0]]
                directory = task.work_dir or Path(task.parent.fileName).parent
                return directory.name + '/' if col == 1 else ''
            if objs[1] == -1:
                return 'segments/' if col == 1 else ''
            file = self.fileTree.items[objs[0]].manifests.get(objs[1])
            if file is None:
                return ''
            return {1: file.displayName, 2: self._DisplayMetadata(file.fileSize),
                    3: self._DisplayMetadata(file.modifyAt)}.get(col, '')
        if col in (5, 6):
            if len(objs) == 2:
                return '' if col == 5 else self.ChildStatus(objs[0], objs[1])
            info = self.TaskInfo(objs[0])
            return info['progress'] if col == 5 else info['status']
        if len(objs) == 1:
            idxi = objs[0]
            parent = self.fileTree.items[idxi].parent
            if parent is None:
                return ''
            if col == 1:
                return parent.displayName or parent.fileName
            elif col == 2:
                return self._DisplayMetadata(parent.fileSize)
            elif col == 3:
                return self._DisplayMetadata(parent.modifyAt)
            else:
                return json.dumps(self.TaskActions(idxi), ensure_ascii=False)
        elif len(objs) == 2:
            idxi = objs[0]
            idxj = objs[1]
            if col == 4:
                actions = self.FileActions(idxi, idxj)
                if actions:
                    return json.dumps(actions, ensure_ascii=False)
            # 处理 Mp4 
            coutputs = len(self.fileTree.items[idxi].outputs)
            if coutputs > 0:
                if idxj < coutputs:
                    if col == 1:
                        # print("#############33", objs, self.fileTree.items[idxi].outputs[idxj])
                        output = self.fileTree.items[idxi].outputs[idxj]
                        return output.displayName or output.fileName
                    elif col == 2:
                        return self._DisplayMetadata(self.fileTree.items[idxi].outputs[idxj].fileSize)
                    elif col == 3:
                        return self._DisplayMetadata(self.fileTree.items[idxi].outputs[idxj].modifyAt)
                    return ''
                else:
                    # MP4 占据前面的子节点位置，减去其数量后才是 childs 的索引。
                    idxj -= coutputs

            # 处理 TS
            if col == 1:
                child = self.fileTree.items[idxi].childs[idxj]
                return child.displayName or child.fileName
            elif col == 2:
                return self._DisplayMetadata(self.fileTree.items[idxi].childs[idxj].fileSize)
            elif col == 3:
                return self._DisplayMetadata(self.fileTree.items[idxi].childs[idxj].modifyAt)
            else:
                # return ''
                if self.fileTree.items[idxi].childs[idxj].fileSize != "-":
                    return ""
                return json.dumps([dict(id='download', label='下载',
                    enabled=self.CanDownloadSegment(idxi, objs[1]))], ensure_ascii=False)
            # else:
            #     return self.fileTree.items[idxi].childs[idxj].absUri
        else:
            return "----"
    
    # 父类函数
    def GetParent(self, item):
        """分片属于 segments，输出和清单属于任务，任务属于虚拟根。"""
        if not item.IsOk():
            return dv.NullDataViewItem
        
        # 使用 ObjectToItem 和 ItemToObject 在数据和视图项之间转换
        keys = self.ItemToObject(item)
        objs = self.ParseKey(keys)
        if len(objs) == 1:
            return dv.NullDataViewItem
        elif len(objs) == 2:
            idxi = objs[0]
            task = self.fileTree.items[idxi]
            parent = (idxi, -1) if objs[1] >= len(task.outputs) else (
                (idxi, -4) if objs[1] in (-1, -2) else (idxi,))
            _key = self._BuildKey(parent)
            # oi = self.ObjectToItem(_key)
            # print("GetParent", idxi, oi.GetID(), int(oi.GetID()))
            # return oi
            return self.ObjectToItem(_key)
        return dv.NullDataViewItem          # 部门的父节点是根
    
    def SetValue(self, variant, item, col):
        """重写模型的数据写入接口，与渲染器的同名 SetValue 不是一回事。

        wx 可编辑单元格通常通过此接口写回数据；本项目主要由下载完成回调主动
        调用，传入 FileItem 更新分片大小、修改时间及完成数量。返回 True 表示
        更新已处理。修改数据后，调用方仍需用 ItemChanged/ValueChanged 通知视图。
        """
        keys = self.ItemToObject(item)
        objs = self.ParseKey(keys)
        # print(f"MultiColumnTreeModel.SetValue", keys, col)
        if len(objs) == 1:
            idxi = objs[0]
            if col == 0:
                # return f"{idxi+1}"
                pass
            elif col == 1:
                # return self.fileTree.items[idxi].parent.fileName
                pass
            elif col == 2:
                self.fileTree.items[idxi].parent.fileSize = "--B"
                return True
            elif col == 3:
                self.fileTree.items[idxi].parent.modifyAt = "----:--:-- --:--"
                return True
            else:
                # return f"{idxi+1}"
                # "--"
                pass
        elif len(objs) == 2:
            idxi = objs[0]
            idxj = objs[1] - len(self.fileTree.items[idxi].outputs)
            if not 0 <= idxj < len(self.fileTree.items[idxi].childs):
                return False
            was_missing = self.fileTree.items[idxi].childs[idxj].fileSize == '-'
            # 记录更新前是否缺失，避免重复回调时重复发送“全部下载完成”事件。
            # self.fileTree.items[idxi].childs[idxj].fileSize = "--B"
            # self.fileTree.items[idxi].childs[idxj].modifyAt = "----:--:-- --:--"
            self.fileTree.items[idxi].childs[idxj].fileSize = variant.fileSize
            self.fileTree.items[idxi].childs[idxj].modifyAt = variant.modifyAt

            # 下载成功一个文件
            self.fileTree.items[idxi].download = sum(
                child.fileSize != '-' for child in self.fileTree.items[idxi].childs)
            if was_missing and self.fileTree.items[idxi].download == len(self.fileTree.items[idxi].childs):
                # return "转MP4"
                self._SendEvent(payload={"fileName": self.fileTree.items[idxi].parent.fileName})
            return True
        return False

    # 动态插入数据的方法
    def InsertChildData(self, parent, newFile):
        """自定义方法：将生成的 MP4 加入任务数据；视图刷新由调用方负责。

        MP4 排在分片前面，插入后子节点索引会变化，需要重新通知视图读取树结构。
        """
        if not parent.IsOk():
            return
        
        keys = self.ItemToObject(parent)
        objs = self.ParseKey(keys)
        if len(objs) == 1:
            idxi = objs[0]
            self.fileTree.items[idxi].outputs.append(newFile)

            # idxj = len(self.fileTree.items[idxi].outputs)+len(self.fileTree.items[idxi].childs)            
            # # 通知视图数据已更改
            # _key = self._BuildKey((idxi, idxj))
            # item = self.ObjectToItem(_key)
            # if item.IsOk():
            #     # self.ItemAdded(parent=parent, item=item)
            #     # self.ItemChanged(item)  # 更新显示状态
            #     self.Cleared()

            #     # # 该方法适合尾部追加
            #     # self.ItemAdded(parent=parent, item=self.ObjectToItem(_key))
            # # self.ItemAdded(parent=parent, item=self.ObjectToItem(newFile))

    # 展开使用
    def GetFirstChild(self, parent):
        """自定义遍历辅助方法：返回首个子节点及下一项的索引 cookie。

        供主窗口展开、折叠、搜索等遍历使用，不是 wx 自动查询树结构的回调。
        """
        children = []
        count = self.GetChildren(parent, children)

        if count > 0:
            return (children[0], 1)  # 返回(子项, cookie)
        return (dv.NullDataViewItem, 0)
    
    # 展开使用
    def GetNextChild(self, item, cookie):
        """自定义遍历辅助方法：item 是父节点，cookie 是下一子节点的索引。

        返回 (子节点, 下一索引)；遍历结束时返回无效节点。
        """
        children = []
        count = self.GetChildren(item, children)

        if cookie < count:
            return (children[cookie], cookie+1)
        return (dv.NullDataViewItem, 0)

    def GetAttr(self, item, col, attr):
        """重写：wx 绘制时查询单元格样式，可在 attr 中设置文字颜色、粗体等。

        返回 True 表示提供了自定义样式，False 表示使用默认样式。
        操作列的自定义渲染器还会自行设置文字颜色，用来区分可用和置灰操作。
        """
        keys = self.ItemToObject(item)
        if not keys:
            return False
        objs = self.ParseKey(keys)
        if col == 6 and len(objs) == 1:
            # 模型第 6 列是“状态”；长度为 1 的节点键表示任务行，分片行不走此分支。
            # 单独返回状态样式，避免继续执行下方任务行的统一蓝色粗体设置。
            status = self.TaskInfo(objs[0])['status']
            # 使用饱和度较高的颜色区分运行、暂停、待处理和完成状态。
            # 保留状态文字，颜色只是辅助提示，不作为唯一的识别方式。
            colours = {
                '检测时长': '#6A35E8',  # 分片已下载，后台正在读取媒体时长。
                '下载中': '#0055FF',  # 鲜蓝：正在下载
                '录制中': '#8800FF',
                '停止中': '#FF6600',
                '合并中': '#8800FF',  # 鲜紫：正在生成 MP4
                '暂停中': '#FF6600',  # 亮橙：等待已发出的请求结束
                '已暂停': '#F00088',  # 亮玫红：请求已结束，任务保持暂停，可恢复
                '已中断': '#B443CF',  # 紫红：上次任务未结束，需手动继续
                '部分完成': '#DAA000',  # 已完成选中的分片，尚未全部下载。
                '待继续': '#DAA000',  # 亮金黄：已有部分分片，等待继续下载
                '待合并': '#00A6B8',  # 亮青：分片齐全，可以合并
                '已完成': '#00AD45',  # 鲜绿：已生成视频
            }
            # 失败优先显示红色，也覆盖“下载中 · 2 个失败”这类组合文案。
            # 未开始、等待下载等未单独配置的状态使用中性灰色。
            colour = '#FF1744' if '失败' in status else colours.get(status, '#707070')
            attr.SetColour(wx.Colour(colour))
            # 状态文字加粗，增强小字号下的颜色辨识度，不添加背景色块。
            # True 告诉 wx 使用这里提供的样式。
            attr.SetBold(True)
            return True
        if len(objs) == 1:
            attr.SetBold(True)
            attr.SetColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_LISTBOXTEXT)
                           if wx.Platform == '__WXMAC__' else wx.BLUE)
            if col == 0:
                # attr.SetColour(wx.BLUE)
                return True
            elif col == 1:
                return True
            elif col == 2:
                # attr.SetAlignment(wx.ALIGN_RIGHT)
                # attr.SetColour(wx.RED)  # 文件大小列右对齐
                return True
        elif len(objs) == 2 and col in (0, 1):
            attr.SetBold(False)
            return True
        return False
