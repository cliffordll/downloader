import wx
import json
# import wx.gizmos as gizmos
import wx.dataview as dv
from src.managers.file_manager import FileManager
from src.managers.path_manager import PathManager
from src.managers.downloader import Downloader
from src.managers.converter import Converter
import os

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
    def __init__(self, parent=None):
        super().__init__()
        self.fileTree = FileManager.GetFileInfos()
        # 因为 ObjectToItem(obj) 在库内部维护一张map，key 为 id(obj)，所以 obj 对象不能变
        # id(obj) 函数返回对象的"标识值"
        self.keyMap = dict()

        self.parent = parent
        self.merge_failed = set()

    def TaskInfo(self, index):
        task = self.fileTree.items[index]
        total = len(task.childs)
        done = sum(child.fileSize != '-' for child in task.childs)
        keys = {Downloader.FileKey(PathManager.GetAbsPath(child.fileName))
                for child in task.childs if child.fileSize == '-'}
        snapshot = Downloader.Snapshot()
        pending = keys & snapshot['pending']
        requesting = keys & snapshot['requesting']
        failed = keys & snapshot['failed']
        output = os.path.join(os.path.dirname(PathManager.GetAbsPath(task.parent.fileName)), 'output.mp4')
        merging = Converter.IsConverting(output)
        if merging:
            status = '合并中'
        elif task.parent.fileName in self.merge_failed:
            status = '合并失败'
        elif task.outputs:
            status = '已完成'
        elif total and done == total:
            status = '待合并'
        elif pending:
            status = ('暂停中' if requesting else '已暂停') if snapshot['paused'] else (
                '下载中' if requesting else '等待下载')
        elif failed:
            status = '下载失败'
        else:
            status = '未开始' if not done else '待继续'
        if failed:
            status += f' · {len(failed)} 个失败'
        return dict(total=total, done=done, percent=int(done * 100 / total) if total else 0,
                    status=status, failed=failed, pending=pending, merging=merging,
                    progress=f'{int(done * 100 / total) if total else 0}% · {done}/{total}')

    def TaskActions(self, index):
        task = self.fileTree.items[index]
        info = self.TaskInfo(index)
        idle = not info['pending'] and not info['merging']
        downloadable = idle and not Downloader.IsPaused() and not task.outputs
        return [
            dict(id='start', label='继续' if info['done'] else '开始',
                 enabled=bool(downloadable and info['done'] < info['total'])),
            dict(id='retry', label='重试', enabled=bool(downloadable and info['failed'])),
            dict(id='delete', label='删除', enabled=idle),
            dict(id='more', label='更多', enabled=True),
        ]

    def _SendEvent(self, payload=None):
        """发送自定义事件的方法"""
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
        _key = ".".join(map(str, keys))
        # 维护 obj 内存地址不变
        if _key not in self.keyMap.keys():
            self.keyMap[_key] = _key
        return self.keyMap[_key]
    
    # 自定义函数
    def ParseKey(self, keyStr: str):
        return tuple(map(int, keyStr.split(".")))

    def GetColumnCount(self):
        return 7  # 原有列、进度、状态
    
    def GetColumnType(self, col):
        return "string"
    
    # 父类函数
    def IsContainer(self, item):
        '''确定节点是否可以展开'''
        if not item.IsOk():
            return True
        
        keys = self.ItemToObject(item)
        objs = self.ParseKey(keys)
        # print("IsContainer", keys)
        if len(objs) <= 1:
            return True
        elif len(objs) == 2:
            pass
        else:
            pass
        return False

    # 父类函数
    def HasContainerColumns(self, item):
        return True

    # 父类函数
    def GetChildren(self, parent, children):
        '''定义树形结构的父子关系'''
        if not parent.IsOk():  # 根节点
            for idx, mu in enumerate(self.fileTree.items):
                _key = self._BuildKey((idx,))
                children.append(self.ObjectToItem(_key))
            return len(self.fileTree.items)

        keys = self.ItemToObject(parent)
        objs = self.ParseKey(keys)
        # print("GetChildren parent:", keys)
        if len(objs) == 1:
            idxi = objs[0]
            # 处理 MP4　文件
            idxj = 0
            for _ in self.fileTree.items[idxi].outputs:
                _key = self._BuildKey((idxi, idxj))
                children.append(self.ObjectToItem(_key))
                idxj += 1

            # 处理 ts 文件
            for _ in self.fileTree.items[idxi].childs:
                _key = self._BuildKey((idxi, idxj))
                children.append(self.ObjectToItem(_key))
                idxj += 1
            # return len(self.fileTree.items[idxi].outputs)+len(self.fileTree.items[idxi].childs)
            return idxj
        elif len(objs) == 2:
            pass
        else:
            pass
        return 0
    
    # 父类函数
    def GetValue(self, item, col):
        '''提供节点数据显示'''
        keys = self.ItemToObject(item)
        objs = self.ParseKey(keys)
        # print("GetValue keys:", keys)
        if col in (5, 6):
            if len(objs) != 1:
                return ''
            info = self.TaskInfo(objs[0])
            return info['progress'] if col == 5 else info['status']
        if len(objs) == 1:
            idxi = objs[0]
            parent = self.fileTree.items[idxi].parent
            if parent is None:
                return ''
            if col == 0:
                return f"{idxi+1}"
            elif col == 1:
                return parent.fileName
            elif col == 2:
                return parent.fileSize
            elif col == 3:
                return parent.modifyAt
            else:
                return json.dumps(self.TaskActions(idxi), ensure_ascii=False)
        elif len(objs) == 2:
            idxi = objs[0]
            idxj = objs[1]
            # 处理 Mp4 
            coutputs = len(self.fileTree.items[idxi].outputs)
            if coutputs > 0:
                if idxj < coutputs:
                    if col == 0:
                        return f"{idxi+1}.{idxj}"
                    elif col == 1:
                        # print("#############33", objs, self.fileTree.items[idxi].outputs[idxj])
                        return self.fileTree.items[idxi].outputs[idxj].fileName
                    elif col == 2:
                        return self.fileTree.items[idxi].outputs[idxj].fileSize
                    elif col == 3:
                        return self.fileTree.items[idxi].outputs[idxj].modifyAt
                    return ''
                else:
                    idxj -= coutputs

            # 处理 TS
            if col == 0:
                return f"{idxi+1}.{idxj+1}"
            elif col == 1:
                return self.fileTree.items[idxi].childs[idxj].fileName
            elif col == 2:
                return self.fileTree.items[idxi].childs[idxj].fileSize
            elif col == 3:
                return self.fileTree.items[idxi].childs[idxj].modifyAt
            else:
                # return f"{idxi+1}.{idxj+1}"
                if self.fileTree.items[idxi].childs[idxj].fileSize != "-":
                    return ""
                return "下载"
            # else:
            #     return self.fileTree.items[idxi].childs[idxj].absUri
        else:
            return "----"
    
    # 父类函数
    def GetParent(self, item):
        '''建立节点的反向链接'''
        if not item.IsOk():
            return dv.NullDataViewItem
        
        # 使用 ObjectToItem 和 ItemToObject 在数据和视图项之间转换
        keys = self.ItemToObject(item)
        objs = self.ParseKey(keys)
        if len(objs) == 1:
            return dv.NullDataViewItem
        elif len(objs) == 2:
            idxi = objs[0]
            _key = self._BuildKey((idxi, ))
            # oi = self.ObjectToItem(_key)
            # print("GetParent", idxi, oi.GetID(), int(oi.GetID()))
            # return oi
            return self.ObjectToItem(_key)
        return dv.NullDataViewItem          # 部门的父节点是根
    
    # 修改TS文件大小，此时没有MP4文件
    def SetValue(self, variant, item, col):
        '''设置item项col列的值为variant'''
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
        """获取第一个子节点"""
        children = []
        count = self.GetChildren(parent, children)

        if count > 0:
            return (children[0], 1)  # 返回(子项, cookie)
        return (dv.NullDataViewItem, 0)
    
    # 展开使用
    def GetNextChild(self, item, cookie):
        """获取下一个子节点"""
        children = []
        count = self.GetChildren(item, children)

        if cookie < count:
            return (children[cookie], cookie+1)
        return (dv.NullDataViewItem, 0)

    def GetAttr(self, item, col, attr):
        """设置显示属性"""
        keys = self.ItemToObject(item)
        if not keys:
            return False
        objs = self.ParseKey(keys)
        if len(objs) == 1:
            attr.SetBold(True)
            attr.SetColour(wx.BLUE)
            if col == 0:
                # attr.SetColour(wx.BLUE)
                return True
            elif col == 1:
                pass
            elif col == 2:
                # attr.SetAlignment(wx.ALIGN_RIGHT)
                # attr.SetColour(wx.RED)  # 文件大小列右对齐
                return True
        elif len(objs) == 2:
            idxi = objs[0]
            idxj = objs[1]
            coutputs = len(self.fileTree.items[idxi].outputs)
            # 设置 MP4 样式
            if coutputs > 0 and idxj < coutputs:
                attr.SetBold(True)
                attr.SetColour(wx.GREEN)

            # 设置 TS 样式
            if col == 0:
                attr.SetBold(True)
                return True
        return False

    # # 刷新整个模型
    # def refresh_all(self):
    #     self.Cleared()  # 通知视图模型已清空（触发重新加载）
    #     # 或者逐项刷新：
    #     self.fileTree = FileManager.GetFileInfo()
    #     # for item in self.data:
    #     #     self.ValueChanged(self.ItemToRow(item), 0)  # 刷新某一列

 
class MultiColumnDataViewCtrl(dv.DataViewCtrl):
    pass
