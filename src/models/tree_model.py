import wx
# import wx.gizmos as gizmos
import wx.dataview as dv
from src.managers.file_manager import FileManager

class MultiColumnTreeModel(dv.PyDataViewModel):
    def __init__(self):
        super().__init__()
        self.fileTree = FileManager.GetFileInfos()
        # 因为 ObjectToItem(obj) 在库内部维护一张map，key 为 id(obj)，所以 obj 对象不能变
        # id(obj) 函数返回对象的"标识值"
        self.keyMap = dict()

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
        return 5  # 序列，文件名，文件大小，最后修改时间, 操作
    
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
            for idxj, fl in enumerate(self.fileTree.items[idxi].childs):
                _key = self._BuildKey((idxi, idxj))
                children.append(self.ObjectToItem(_key))
            return len(self.fileTree.items[idxi].childs)
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
        if len(objs) == 1:
            idxi = objs[0]
            if col == 0:
                return f"{idxi+1}"
            elif col == 1:
                return self.fileTree.items[idxi].parent.fileName
            elif col == 2:
                return self.fileTree.items[idxi].parent.fileSize
            elif col == 3:
                return self.fileTree.items[idxi].parent.modifyAt
            else:
                # return f"{idxi+1}"
                if self.fileTree.items[idxi].download == len(self.fileTree.items[idxi].childs):
                    return "转MP4"
                return "下载全部"
            # else:
            #     return self.fileTree.items[idxi].parent.absUri
        elif len(objs) == 2:
            idxi = objs[0]
            idxj = objs[1]
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
            idxj = objs[1]
            # self.fileTree.items[idxi].childs[idxj].fileSize = "--B"
            # self.fileTree.items[idxi].childs[idxj].modifyAt = "----:--:-- --:--"
            self.fileTree.items[idxi].childs[idxj].fileSize = variant.fileSize
            self.fileTree.items[idxi].childs[idxj].modifyAt = variant.modifyAt
            return True
        return False

    # 动态插入数据的方法
    def AddData(self, data: list):
        '''添加数据'''
        pass

        # '''定义树形结构的父子关系'''
        # if not parent.IsOk():  # 根节点
        #     for idx, mu in enumerate(self.fileTree.items):
        #         _key = self._BuildKey((idx,))
        #         children.append(self.ObjectToItem(_key))
        #     return len(self.fileTree.items)

        # keys = self.ItemToObject(parent)
        # objs = self.ParseKey(keys)
        # # print("GetChildren parent:", keys)
        # if len(objs) == 1:
        #     idxi = objs[0]
        #     for idxj, fl in enumerate(self.fileTree.items[idxi].childs):
        #         _key = self._BuildKey((idxi, idxj))
        #         children.append(self.ObjectToItem(_key))
        #     return len(self.fileTree.items[idxi].childs)
        # elif len(objs) == 2:
        #     pass
        # else:
        #     pass
        # return 0


        # for item in data:
        #     idxi = len(self.data)
        #     self.data.append(item)
        #     _key = self._BuildKey((idxi,))
        #     parent = self.ObjectToItem(_key)
        #     self.ItemAdded(dv.NullDataViewItem, parent)
        #     # self.ItemAdded(dv.NullDataViewItem, self.ObjectToItem(_key))

        #     for child in item["childs"]:
        #         self.ItemAdded(parent, self.ObjectToItem(child))

    # def AddChildData(self, key, data: list):
    #     for idx, item in enumerate(self.data):
    #         if item["category"] == key:
    #             _key = self._BuildKey((idx, ))
    #             parent = self.ObjectToItem(_key)

    #             for child in item["childs"]:
    #                 self.ItemAdded(parent, self.ObjectToItem(child))

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