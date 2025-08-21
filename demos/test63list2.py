import wx
import wx.dataview as dv

# from models.file_manager import FileItem
from models.file_manager import FileItem, FileManager

class MultiColumnTreeModel(dv.PyDataViewModel):
    def __init__(self):
        super().__init__()
        self.fileTree = FileManager.GetFileList()
        # 因为 ObjectToItem(obj) 在库内部维护一张map，key 为 id(obj)，所以 obj 对象不能变
        # id(obj) 函数返回对象的"标识值"
        self.keyMap = dict()

    def _BuildKey(self, keys: tuple):
        _key = ".".join(map(str, keys))
        # 维护 obj 内存地址不变
        if _key not in self.keyMap.keys():
            self.keyMap[_key] = _key
        return self.keyMap[_key]
    
    def _ParseKey(self, keyStr: str):
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
        objs = self._ParseKey(keys)
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
        objs = self._ParseKey(keys)
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
        objs = self._ParseKey(keys)
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
                return f"{idxi+1}"
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
                return f"{idxi+1}.{idxj+1}"
        else:
            return "----"
    
    # 父类函数
    def GetParent(self, item):
        '''建立节点的反向链接'''
        if not item.IsOk():
            return dv.NullDataViewItem
        
        # 使用 ObjectToItem 和 ItemToObject 在数据和视图项之间转换
        keys = self.ItemToObject(item)
        objs = self._ParseKey(keys)
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

class MultiColumnTreeFrame(wx.Frame):
    def __init__(self, title: str):
        super().__init__(None, size=(600, 400))
        self.SetTitle(title)
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 创建DataViewCtrl
        self.dvc = dv.DataViewCtrl(panel, style=wx.BORDER_THEME|dv.DV_ROW_LINES|dv.DV_VERT_RULES|dv.DV_VARIABLE_LINE_HEIGHT|dv.DV_ROW_LINES)
        
        # 创建并关联模型
        self.model = MultiColumnTreeModel()
        self.dvc.AssociateModel(self.model)
        self.model.DecRef()  # 避免内存泄漏
        self.OnExpandAll()

        # 添加多列
        self.dvc.AppendTextColumn("序列", 0, width=80)
        # # 自定义薪资列
        # renderer = dv.DataViewTextRenderer()
        # renderer.EnableEllipsize(wx.ELLIPSIZE_END)
        # self.dvc.AppendColumn(dv.DataViewColumn("文件名", renderer, 1, width=180, align=wx.ALIGN_LEFT))
        self.dvc.AppendTextColumn("文件名", 1, width=180)
        self.dvc.AppendTextColumn("文件大小", 2, width=100, align=wx.ALIGN_RIGHT)
        self.dvc.AppendTextColumn("修改时间", 3, width=120)
        self.dvc.AppendTextColumn("操作", 4, width=50)
        
        # 添加搜索框
        search_box = wx.SearchCtrl(panel)
        search_box.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.on_search)
        search_box.Bind(wx.EVT_TEXT, self.on_search_text)
        
        # 布局
        sizer.Add(search_box, 0, wx.EXPAND|wx.ALL, 5)
        sizer.Add(self.dvc, 1, wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM, 5)
        panel.SetSizer(sizer)
        
        self.Center()

    def _RecursiveExpand(self, item, expand):
        """递归展开/折叠"""
        child, cookie = self.model.GetFirstChild(item)
        while child.IsOk():            
            if self.model.IsContainer(child):
                self.dvc.Expand(child) if expand else self.dvc.Collapse(child)
            else:
                self._RecursiveExpand(child, expand)
            child, cookie = self.model.GetNextChild(item, cookie)

    def OnExpandAll(self):
        """展开所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, True)
    
    def OnCollapseAll(self):
        """折叠所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, False)
    
    def on_search(self, event):
        """搜索按钮事件"""
        # print("on_search", event.GetString())
        self._SearchItems(event.GetString())
    
    def on_search_text(self, event):
        """搜索文本变化事件"""
        # print("on_search_text", event.GetString())
        self._SearchItems(event.GetString())
    
    def _SearchItems(self, text):
        """搜索匹配项"""
        if not text:
            return
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        found_item = self._FindItem(root, text.lower())
        
        if found_item.IsOk():
            self.dvc.Select(found_item)
            self.dvc.EnsureVisible(found_item)
    
    def _FindItem(self, parent, search_text):
        """递归查找匹配项"""
        # child, cookie = self.dvc.GetFirstChild(parent)
        child, cookie = self.model.GetFirstChild(parent)
        while child.IsOk():
            # 检查当前项
            for col in range(self.model.GetColumnCount()):
                value = self.model.GetValue(child, col).lower()
                if search_text in value:
                    return child
            
            # 如果是容器，递归检查子项
            if self.model.IsContainer(child):
                found_in_child = self._FindItem(child, search_text)
                if found_in_child.IsOk():
                    return found_in_child
            
            child, cookie = self.model.GetNextChild(parent, cookie)
        
        return dv.NullDataViewItem

if __name__ == "__main__":
    app = wx.App()
    frame = MultiColumnTreeFrame(title="TreeDataModel")
    frame.Show()
    app.MainLoop()