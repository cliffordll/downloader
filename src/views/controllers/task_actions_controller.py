"""任务菜单、删除、分片操作和媒体处理结果的界面控制器。"""

import sqlite3
from pathlib import Path
from threading import Thread

import wx

from src.core.file_export import save_file_as
from src.core.path_manager import PathManager
from src.media.m3u8.m3u8_downloader import M3U8Downloader
from src.schemas.task import FileStatus, TaskStatus, TaskType
from src.storage.task_repository import TaskDataError, TaskConflictError
from src.views.dialogs.segment_edit_dialog import SegmentEditDialog
from src.views.dialogs.task_rename_dialog import TaskRenameDialog
from src.views.dialogs.task_group_dialog import delete_task_group, rename_task_group


class TaskActionsController:
    """处理任务级动作；任务执行仍由 TaskRunner 统一协调。"""

    def __init__(self, frame):
        self.frame = frame

    def _SaveFileAs(self, source):
        source = Path(source)
        if not source.is_file():
            wx.MessageBox('原文件已不存在，请重新下载后再另存为。', '另存为失败', wx.OK | wx.ICON_ERROR, self.frame)
            return
        dialog = wx.FileDialog(self.frame, '另存为', defaultFile=source.name,
                               wildcard='所有文件 (*.*)|*.*',
                               style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            destination = dialog.GetPath()
        finally:
            dialog.Destroy()
        errors = []

        def copy():
            try:
                save_file_as(source, destination)
            except (OSError, ValueError) as error:
                errors.append(str(error))

        progress = wx.ProgressDialog('另存为', '正在复制文件，请稍候…', parent=self.frame,
                                     style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME)
        worker = Thread(target=copy, name='file-export')
        try:
            worker.start()
            while worker.is_alive():
                progress.Pulse()
                worker.join(0.05)
        finally:
            progress.Destroy()
        if errors:
            wx.MessageBox(errors[0], '另存为失败', wx.OK | wx.ICON_ERROR, self.frame)

    def _ViewPlaylist(self, path):
        path = Path(path)
        try:
            text = path.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError) as error:
            wx.MessageBox(f'无法读取播放列表：{error}', '查看内容失败', parent=self.frame)
            return
        self.frame._ShowInformation('查看内容：' + path.name, text, line_numbers=True)

    def _EditSegmentInfo(self, task_id, sequence):
        try:
            record = self.frame.tasks.repository.get(task_id)
            if record is None:
                raise ValueError('任务不存在，请刷新后重试。')
            segment = next((s for s in record.details.media_segments if s.sequence == sequence), None)
            if segment is None:
                raise ValueError('分片不存在，请刷新后重试。')
            dialog = SegmentEditDialog(self.frame, segment.source_url or '', segment.duration,
                                       completed=segment.status == FileStatus.COMPLETED)
            try:
                while dialog.ShowModal() == wx.ID_OK:
                    try:
                        self.frame.runner.update_segment_info(task_id, sequence, dialog.GetURL(), dialog.GetDuration())
                    except ValueError as error:
                        wx.MessageBox(str(error), '无法修改分片信息', parent=dialog)
                        continue
                    self.frame._SyncDownloads()
                    break
            finally:
                dialog.Destroy()
        except (ValueError, OSError, sqlite3.Error, TaskDataError, TaskConflictError) as error:
            wx.MessageBox(str(error), '无法修改分片信息', parent=self.frame)

    def _DetectSegmentDurations(self, task_id):
        try:
            self.frame.runner.detect_durations(task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '无法检测分片时长', parent=self.frame)
            return
        if not hasattr(self.frame, '_manualDurationChecks'):
            self.frame._manualDurationChecks = set()
        self.frame._manualDurationChecks.add(task_id)
        self.frame._SetPrimaryStatusText('正在检测已下载分片的播放时长…')
        self.frame.OnTaskProgress(None)

    def _CheckAndContinue(self, task_id):
        """已完成任务也可显式检查并补下载，不在展示时检查磁盘。"""
        try:
            if self.frame.runner.start(task_id):
                self.frame._completion_notified.discard(task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '任务操作失败', parent=self.frame)
        self.frame._SyncDownloads()

    def OnTaskMenu(self, item):
        if not item.IsOk():
            return
        keys = self.frame.model.ParseKey(self.frame.model.ItemToObject(item))
        if self.frame.model.IsGroupKey(keys):
            return
        if len(keys) not in (1, 2):
            return
        index = keys[0]
        task = self.frame.model.fileTree.items[index]
        directory = task.save_dir or Path(PathManager.GetAbsPath(task.parent.fileName)).parent
        menu = wx.Menu()

        def add(label, callback, enabled=True, *, new_group=False):
            if new_group and menu.GetMenuItemCount():
                menu.AppendSeparator()
            entry = menu.Append(wx.ID_ANY, label)
            entry.Enable(enabled)
            menu.Bind(wx.EVT_MENU, lambda event: callback(), entry)

        # keys=[任务索引] 表示任务；[任务索引, 节点键] 表示该任务的文件或目录。
        # 节点键不表示目录深度；工作目录里的分片也使用两个元素的 keys。
        if len(keys) == 2:
            key = keys[1]
            # 负数是特殊节点：-4 为工作目录，-1 为 segments 分片目录。
            if key in (-4, -1):
                directory = task.work_dir or Path(task.parent.fileName).parent
                if key == -1:
                    directory = directory / 'segments'
                add('打开文件夹', lambda: self.frame._OpenLocalPath(directory))
            elif key in task.manifests:
                # 清单节点单独登记在 manifests 中，当前 -2 对应 download.m3u8。
                path = Path(task.manifests[key].fileName)
                add('打开/播放', lambda: self.frame._OpenTaskManifest(task, path))
                add('选择播放器…', lambda: self.frame._ChoosePlayerAndOpenManifest(task, path), new_group=True)
                add('打开所在文件夹', lambda: self.frame._OpenLocalPath(path.parent))
                add('查看内容', lambda: self.frame._ViewPlaylist(path), new_group=True)
            elif 0 <= key < len(task.outputs) + len(task.childs):
                # 非负键先排列 outputs（成品或 .part），再排列 childs（TS 分片）。
                # 减去输出数量后，负数表示输出文件，非负数才是分片列表的索引。
                # 例如有 1 个输出：key=0 是输出，key=1 是第 0 个分片。
                segment_index = key - len(task.outputs)
                file = task.outputs[key] if segment_index < 0 else task.childs[segment_index]
                path = Path(file.fileName)
                # 未完成的直链 MP4 输出行代表 .part，不提供播放和另存为。
                partial = segment_index < 0 and task.task_type == TaskType.MP4 and task.task_status != TaskStatus.COMPLETED
                pending = segment_index >= 0 and file.fileSize == '-'
                if pending:
                    # '-' 是展示模型的未完成分片标记；此处不访问磁盘检查文件。
                    info = self.frame.model.TaskInfo(index)
                    add('下载', lambda: self.frame.OnSegmentDownload(item),
                        self.frame.model.CanDownloadSegment(index, key))
                else:
                    if not partial:
                        add('打开/播放', lambda: self.frame._OpenVideo(path, task.task_id))
                        add('选择播放器…', lambda: self.frame._ChoosePlayerAndOpen(path, task.task_id), new_group=True)
                    add('打开所在文件夹', lambda: self.frame._OpenLocalPath(path.parent))
                # 分片使用自己的 URL；直链 MP4（含 .part）使用任务 URL。
                # 合成 MP4 是本地生成文件，没有独立下载地址。
                source_url = file.absUri if segment_index >= 0 else (
                    task.parent.absUri if task.task_type == TaskType.MP4 else '')
                if source_url:
                    add('查看下载地址', lambda: self.frame._ShowInformation('下载地址', source_url), new_group=True)
                if segment_index >= 0:
                    info = self.frame.model.TaskInfo(index)
                    add('修改分片信息', lambda: self.frame._EditSegmentInfo(task.task_id, file.sequence),
                        not info['merging'] and not info['pending'], new_group=not source_url)
                if not pending and not partial:
                    add('另存为…', lambda: self.frame._SaveFileAs(path), new_group=True)
            try:
                if menu.GetMenuItemCount():
                    self.frame.mcTree.PopupMenu(menu)
            finally:
                menu.Destroy()
            # 文件/目录菜单处理完即返回，不再追加下面的任务菜单。
            return

        info = self.frame.model.TaskInfo(index)
        parent_item = self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,)))
        record = self.frame.tasks.repository.get(task.task_id) if task.task_id else None
        if record is not None:
            group_menu = wx.Menu()
            ungrouped = group_menu.AppendRadioItem(wx.ID_ANY, '未分组')
            ungrouped.Check(record.group_id is None)
            ungrouped.Enable(task.rename_allowed and record.group_id is not None)
            group_menu.Bind(wx.EVT_MENU,
                            lambda event: self._MoveTaskToGroup(task.task_id, None), ungrouped)
            for group in self.frame.tasks.repository.list_groups():
                entry = group_menu.AppendRadioItem(wx.ID_ANY, group.name)
                entry.Check(group.id == record.group_id)
                entry.Enable(task.rename_allowed and group.id != record.group_id)
                group_menu.Bind(wx.EVT_MENU,
                                lambda event, group_id=group.id:
                                    self._MoveTaskToGroup(task.task_id, group_id), entry)
            if group_menu.GetMenuItemCount():
                menu.AppendSeparator()
                menu.AppendSubMenu(group_menu, '移动到分组')
        add('重命名…', lambda: self.frame._RenameTask(task.task_id, task.parent.displayName),
            task.rename_allowed)
        add('解密任务' if task.encrypted else '加密任务',
            lambda: self.frame._SetTaskEncrypted(task.task_id, not task.encrypted),
            task.rename_allowed, new_group=True)
        add('打开文件夹', lambda: self.frame._OpenLocalPath(directory))
        if task.outputs and (task.task_type != TaskType.MP4 or task.task_status == TaskStatus.COMPLETED):
            for output in task.outputs:
                path = PathManager.GetAbsPath(output.fileName)
                add('播放视频', lambda path=path: self.frame._OpenVideo(path, task.task_id))
                add('选择播放器',
                    lambda path=path: self.frame._ChoosePlayerAndOpen(path, task.task_id), new_group=True)
        else:
            add('播放视频', lambda: None, False)
        if task.parent.absUri:
            add('查看下载地址', lambda: self.frame._ShowInformation('下载地址', task.parent.absUri), new_group=True)
        if task.task_type == TaskType.MP4 and task.last_error:
            add('查看失败原因', lambda: self.frame._ShowInformation('MP4 下载失败', task.last_error),
                new_group=not task.parent.absUri)
        if task.task_type == TaskType.M3U8:
            add('检测分片时长', lambda: self.frame._DetectSegmentDurations(task.task_id),
                bool(info['done']) and not info['pending'] and not info['merging'], new_group=True)
            add('转 MP4', lambda: self.frame.OnTaskAction(parent_item, 'merge'),
                bool(info['total']) and info['done'] == info['total'] and not task.outputs
                and not task.encrypted and not info['pending'] and not info['merging'])
        if task.task_type in (TaskType.M3U8, TaskType.MP4) and task.task_status in (TaskStatus.COMPLETED, TaskStatus.WAITING_MERGE):
            add('检查并继续下载', lambda: self.frame._CheckAndContinue(task.task_id),
                new_group=task.task_type != TaskType.M3U8)
        completed_files = []
        if task.task_type == TaskType.M3U8 or task.task_status == TaskStatus.COMPLETED:
            completed_files = [Path(PathManager.GetAbsPath(output.fileName)) for output in task.outputs]
            if not completed_files and task.task_type != TaskType.M3U8:
                completed_files = [Path(PathManager.GetAbsPath(task.parent.fileName))]
        if completed_files:
            for position, path in enumerate(completed_files):
                label = '另存为…' if len(completed_files) == 1 else '另存为：' + path.name
                add(label, lambda path=path: self.frame._SaveFileAs(path), new_group=position == 0)
        else:
            add('另存为…', lambda: None, False, new_group=True)
        try:
            self.frame.mcTree.PopupMenu(menu)
        finally:
            menu.Destroy()

    def _MoveTaskToGroup(self, task_id, group_id):
        self.frame._SetPrimaryStatusText('正在移动任务，请稍候…')
        # 只立即绘制状态栏，不进入嵌套事件循环，避免移动过程中重复操作任务。
        self.frame.statusBar.Update()
        busy_cursor = wx.BusyCursor()
        try:
            self.frame.runner.move_task_to_group(task_id, group_id)
            self.frame.OnRefresh(None)
            if (self.frame._groupFilter == 'all'
                    or self.frame._groupFilter == group_id
                    or (self.frame._groupFilter == 'ungrouped' and group_id is None)):
                group_key = self.frame.model._BuildKey(self.frame.model._GroupKey(group_id))
                group_item = self.frame.model.ObjectToItem(group_key)
                if self.frame.model.IsContainer(group_item):
                    self.frame._SetTaskExpanded(group_item, True)
            self.frame._SyncDownloads()
            self.frame._SetPrimaryStatusText('任务移动完成')
        except (ValueError, OSError, RuntimeError, sqlite3.Error,
                TaskDataError, TaskConflictError) as error:
            self.frame._SetPrimaryStatusText(f'移动任务失败：{error}')
            wx.MessageBox(f'移动任务失败：{error}', '分组操作失败',
                          wx.OK | wx.ICON_WARNING, parent=self.frame)
        finally:
            del busy_cursor

    def _RenameTask(self, task_id, current_name):
        dialog = TaskRenameDialog(self.frame, current_name)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            name = dialog.GetName().strip()
            if not name:
                return
            self.frame.runner.rename(task_id, name)
            self.frame.OnRefresh(None)
            self.frame._SyncDownloads()
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError, TaskConflictError) as error:
            wx.MessageBox(str(error), '重命名失败', parent=self.frame)
        finally:
            dialog.Destroy()

    def OnTaskAction(self, item, action_id='start'):
        """自定义操作分发入口，不是 wx 自动调用的重写方法。

        行内渲染器识别点击区域后传入 item 和操作 id；“转 MP4”菜单传入 merge。
        item 指明具体任务行，不依赖当前选中行；默认 start 表示开始/暂停/继续。
        start 表示开始/暂停/继续，retry 重试失败，delete 删除，more 打开菜单。
        """
        if not item.IsOk():
            return
        keys = self.frame.model.ParseKey(self.frame.model.ItemToObject(item))
        if self.frame.model.IsGroupKey(keys):
            group = self.frame.model.groups_by_key.get(keys[1]) if keys[0] == -1 else None
            if group is None:
                return
            try:
                if action_id in ('move_group_up', 'move_group_down'):
                    offset = -1 if action_id == 'move_group_up' else 1
                    if self.frame.tasks.repository.move_group(group.id, offset):
                        self.frame.OnRefresh(None)
                elif action_id == 'rename_group':
                    if rename_task_group(self.frame, self.frame.tasks.repository, group) is not None:
                        self.frame.OnRefresh(None)
                elif action_id == 'delete_group':
                    delete_task_group(self.frame, self.frame.tasks.repository,
                                      self.frame.tasks, self.frame.runner, group)
            except Exception as error:
                wx.MessageBox(f'分组操作失败：{error}', '分组操作失败',
                              wx.OK | wx.ICON_WARNING, parent=self.frame)
            return
        if len(keys) == 2:
            action = next((entry for entry in self.frame.model.FileActions(*keys) if entry['id'] == action_id), None)
            if action is not None and action['enabled']:
                task = self.frame.model.fileTree.items[keys[0]]
                if self.frame._manualActionClicks:
                    self.frame._QueueTaskPopup(task, action_id)
                else:
                    self.frame.OnDeleteTask(task, scope='work' if action_id == 'delete_work' else 'output')
            return
        # 分片下载由另一个处理函数负责。
        if len(keys) != 1:
            return
        index = keys[0]
        task = self.frame.model.fileTree.items[index]
        info = self.frame.model.TaskInfo(index)
        # 点击与绘制之间状态可能改变，所以在执行前重新读取当前任务状态。
        if action_id == 'merge':
            if task.task_type != TaskType.M3U8:
                return
            # 合并位于“更多”菜单内：分片必须完整，不能正在下载/合并或已有输出。
            if info['pending'] or info['merging'] or task.outputs or not info['total'] or info['done'] != info['total']:
                return
            self.frame._CreateMP4File(task.parent.fileName, item)
        else:
            # 按稳定的操作 id 匹配，不依赖“下载”“继续”等会随状态变化的显示文字。
            # 再检查 enabled，防止过期的界面状态触发已不可用的操作。
            action = next((entry for entry in self.frame.model.TaskActions(index) if entry['id'] == action_id), None)
            if action is None or not action['enabled']:
                return
            if action_id == 'more':
                if self.frame._manualActionClicks:
                    self.frame._QueueTaskPopup(task, action_id)
                else:
                    self.frame.OnTaskMenu(item)
                return
            if action_id == 'delete':
                # 删除处理函数会再次检查运行状态，并确认是否回收本地文件。
                if self.frame._manualActionClicks:
                    self.frame._QueueTaskPopup(task, action_id)
                else:
                    self.frame.OnDeleteTask(task)
                return
        if action_id in ('start', 'retry'):
            try:
                if self.frame.runner.activate(task.task_id, retry=action_id == 'retry'):
                    self.frame._completion_notified.discard(task.task_id)
            except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
                wx.MessageBox(str(error), '任务操作失败', parent=self.frame)
            self.frame._SyncDownloads()
        # 通知 wx 重新读取本行数据，使操作文字、置灰状态和任务状态及时更新。
        self.frame.model.ItemChanged(item)

    def _QueueTaskPopup(self, task, action_id):
        # Cocoa 必须先结束鼠标事件处理，再进入菜单跟踪或模态对话框循环。
        identity = task.task_id or task.parent.fileName
        wx.CallAfter(self.frame._ShowTaskPopup, identity, action_id)

    def _ShowTaskPopup(self, identity, action_id):
        if self.frame._closing or not self.frame or self.frame.IsBeingDeleted():
            return
        # 延迟期间可能刷新或删除任务，按身份重新定位，不能沿用旧行号。
        for index, task in enumerate(self.frame.model.fileTree.items):
            if (task.task_id or task.parent.fileName) != identity:
                continue
            actions = (self.frame.model.FileActions(index, -4 if action_id == 'delete_work' else 0)
                       if action_id in ('delete_work', 'delete_output') else self.frame.model.TaskActions(index))
            action = next((entry for entry in actions
                           if entry['id'] == action_id), None)
            if action is None or not action['enabled']:
                return
            if action_id == 'more':
                self.frame.OnTaskMenu(self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,))))
            elif action_id == 'delete':
                self.frame.OnDeleteTask(task)
            elif action_id in ('delete_work', 'delete_output'):
                self.frame.OnDeleteTask(task, scope='work' if action_id == 'delete_work' else 'output')
            return

    def OnDeleteTask(self, task, *, scope='task'):
        """确认是否同时回收任务文件；仅清理明确归属于该任务的路径。"""
        try:
            # 暂停中的 M3U8 任务仍保留在内存等待队列里，runner.delete 会安全移除
            # 这部分队列；这里若直接用 busy() 拦截，就会导致必须重启后才能删除。
            if (scope == 'task' and self.frame.runner.busy(task.task_id)
                    and task.task_type != TaskType.M3U8):
                raise ValueError('任务仍在下载、检测或合并中，暂时不能删除。')
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '无法删除', wx.OK | wx.ICON_WARNING, parent=self.frame)
            return
        title = {'task': '删除任务', 'work': '删除工作文件夹', 'output': '删除 MP4 文件'}[scope]
        dialog = wx.RichMessageDialog(self.frame, f'确定{title}？', title,
                                      wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        if scope == 'task':
            dialog.SetExtendedMessage('将从列表和数据库中删除此任务。勾选下方选项会将任务专属工作文件夹（含全部内容）、视频和对应临时文件移入回收站；工作文件夹以外的另存为副本保留。文件回收失败时保留任务记录。')
            dialog.ShowCheckBox('同时删除此任务的本地文件（移入回收站）', checked=True)
        elif scope == 'work':
            dialog.SetExtendedMessage('将此任务的专属工作文件夹及全部内容移入回收站，包括隐藏文件和临时文件。保留任务记录和文件夹外的 MP4。已暂停的等待队列会移除。')
        else:
            dialog.SetExtendedMessage('将此任务的 MP4 和对应临时文件移入回收站。保留任务记录和分片，分片完整时可以重新转 MP4；另存为副本保留。')
        dialog.SetYesNoLabels('删除任务' if scope == 'task' else '移入回收站', '取消')
        try:
            if dialog.ShowModal() != wx.ID_YES:
                return
            delete_files = dialog.IsCheckBoxChecked() if scope == 'task' else True
        finally:
            dialog.Destroy()
        try:
            if scope == 'task':
                self.frame.runner.delete(task.task_id, delete_files=delete_files)
            else:
                # 主动删除产物后不自动合并重建，由用户选择转 MP4 或继续下载。
                self.frame._completion_notified.add(task.task_id)
                self.frame.runner.delete(task.task_id, scope=scope)
            self.frame.model.merge_failed.discard(task.parent.fileName)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(f'删除未完成：{error}', '无法删除', wx.OK | wx.ICON_WARNING, parent=self.frame)
            return
        self.frame.OnRefresh(None)

    def OnSegmentDownload(self, item):
        """只由分片的“下载”操作调用，重新核对索引和是否仍需要下载。"""
        if not item.IsOk():
            return
        keys = self.frame.model.ParseKey(self.frame.model.ItemToObject(item))
        if len(keys) != 2:
            return
        if not self.frame.model.CanDownloadSegment(keys[0], keys[1]):
            return
        task = self.frame.model.fileTree.items[keys[0]]
        index = keys[1] - len(task.outputs)
        if 0 <= index < len(task.childs) and task.childs[index].fileSize == '-' and task.parent:
            self.frame._DownloadFiles(task.parent.fileName, [(index, item)])

    def OnAllTSDownload(self, event):
        '''所有TS文件都已经下载完毕，修改操作文本'''
        payload = event.GetData()
        if not payload:
            return
        fileName = payload.get("fileName", "")
        if payload.get('task_id') is not None:
            task = next((task for task in self.frame.model.fileTree.items if task.task_id == payload['task_id']), None)
            if task is None:
                return  # 任务已删除，不处理之前排入 wx 队列的完成事件。
            fileName = task.parent.fileName
            if task.duration_pending:
                self.frame._completion_notified.discard(task.task_id)
                self.frame._QueueDurationCheck(task)
                return  # 实际时长入库后再重建清单，并执行自动合并。
        task = next((task for task in self.frame.model.fileTree.items if task.parent.fileName == fileName), None)
        if task is None:
            return
        try:
            self.frame.runner.complete(task.task_id)
        except (ValueError, OSError, RuntimeError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '任务处理失败', parent=self.frame)
        # 方法2：记录上一次的展开折叠状态
        self.frame._RefreshWithState()

    def _QueueDurationCheck(self, task):
        """展示层只指出哪些行还需检测，去重、线程池及关闭处理由协调对象负责。"""
        if task.duration_pending and task.task_id:
            self.frame.runner.queue_duration(task.task_id)

    def _DurationChecked(self, task_id, record, error):
        manual = task_id in getattr(self.frame, '_manualDurationChecks', set())
        if manual:
            self.frame._manualDurationChecks.discard(task_id)
        if not self.frame or self.frame.IsBeingDeleted() or self.frame.runner.closed.is_set():
            return
        if error:
            self.frame.runner.block_duration(task_id)
            wx.MessageBox(f'时长检测结果保存失败：{error}\n请刷新后重试。', '检测失败', parent=self.frame)
            return
        # 后台快照可能早于其他分片的下载回调，必须重新读取最新记录再更新列表。
        try:
            record = self.frame.tasks.repository.get(task_id)
        except (ValueError, OSError, sqlite3.Error, TaskDataError) as exc:
            self.frame._DurationChecked(task_id, None, str(exc))
            return
        if record is None:
            return
        index = self.frame.model.ApplyTaskRecord(record)
        if index is None:
            return
        task = self.frame.model.fileTree.items[index]
        if task.duration_pending and not manual:
            self.frame._QueueDurationCheck(task)
            return
        playlist_saved = self.frame._CreateM3U8File(task.parent.fileName)
        failures = sum(s.duration_status == 'failed' for s in record.details.media_segments)
        if failures:
            self.frame._SetPrimaryStatusText(f'{task.parent.displayName}：{failures} 个分片时长检测失败，保留原时长。')
        elif manual and playlist_saved:
            self.frame._SetPrimaryStatusText('分片时长检测完成，本地播放列表已更新。')
        if manual:
            self.frame._completion_notified.add(task_id)
        pending = M3U8Downloader.Snapshot()['pending']
        if (not manual and task.task_status == TaskStatus.WAITING_MERGE
                and task.childs and task.download == len(task.childs) and not task.outputs
                and task_id not in self.frame._completion_notified
                and not any(M3U8Downloader.FileKey(child.fileName) in pending for child in task.childs)):
            self.frame._completion_notified.add(task_id)
            self.frame.model._SendEvent({'task_id': task_id, 'fileName': task.parent.fileName})
        self.frame.model.ItemChanged(self.frame.model.ObjectToItem(self.frame.model._BuildKey((index,))))

    def _M3U8Changed(self, task_id):
        """下载结果已在后台保存；这里只触发时长检测和现有的自动合并流程。"""
        for task in self.frame.model.fileTree.items:
            if task.task_id != task_id:
                continue
            if task_id in getattr(self.frame, '_manualDurationChecks', set()):
                break
            pending = M3U8Downloader.Snapshot()['pending']
            if (task.task_status == TaskStatus.WAITING_MERGE
                    and task.childs and task.download == len(task.childs) and not task.outputs
                    and task_id not in self.frame._completion_notified
                    and not any(M3U8Downloader.FileKey(child.fileName) in pending for child in task.childs)):
                if task.duration_pending:
                    self.frame._QueueDurationCheck(task)
                    break  # 检测回调负责发送完成事件，避免自动合并早于时长入库。
                self.frame._completion_notified.add(task_id)
                self.frame.model._SendEvent({'task_id': task_id, 'fileName': task.parent.fileName})
            break

