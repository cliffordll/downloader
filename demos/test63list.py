import wx
import wx.dataview as dv

# from models.file_manager import FileItem
from models.file_manager import FileItem, FileManager

class MultiColumnTreeModel(dv.PyDataViewModel):
    def __init__(self):
        # dv.PyDataViewModel.__init__(self)
        super().__init__()
        self.fileTree = FileManager.GetFileList()
    
    def GetColumnCount(self):
        return 5  # 序列，文件名，文件大小，最后修改时间, 操作
    
    def GetColumnType(self, col):
        return "string"
    
    def _BuildKey(self, keys: tuple):
        # return ".".join(keys)
        return ".".join(map(str, keys))
    
    def _ParseKey(self, keyStr: str):
        # return keyStr.split('.')
        return tuple(map(int, keyStr.split(".")))
    
    # 父类函数
    def IsContainer(self, item):
        '''确定节点是否可以展开'''
        if not item.IsOk():
            return True
        
        # objs = self.ItemToObject(item)
        keys = self.ItemToObject(item)
        objs = self._ParseKey(keys)
        # print("IsContainer", keys)
        if len(objs) <= 1:
            return True
        elif len(objs) == 2:
            # return True
            pass
        else:
            pass
        return False

        # ################### OK1
        # obj = self.ItemToObject(item)
        # if isinstance(obj, int):    # 一级节点是容器
        #     return True
        # return False                # 二级节点不是容器

    # 父类函数
    def HasContainerColumns(self, item):
        return True

    # 父类函数
    def GetChildren(self, parent, children):
        '''定义树形结构的父子关系'''
        if not parent.IsOk():  # 根节点
            print("##########################3 no ok")
            for idx, mu in enumerate(self.fileTree.items):
                # children.append(self.ObjectToItem((idx, )))
                _key = self._BuildKey((idx,))

                oi = self.ObjectToItem(_key)
                print(idx, oi.GetID(), int(oi.GetID()))
                children.append(oi)
                # children.append(self.ObjectToItem(_key))
                # print("GetChildren", "root", idx, mu.parent.fileName)
            return len(self.fileTree.items)

        keys = self.ItemToObject(parent)
        objs = self._ParseKey(keys)
        print("GetChildren parent:", keys)
        if len(objs) == 1:
            idxi = objs[0]
            for idxj, fl in enumerate(self.fileTree.items[idxi].childs):
                # children.append(self.ObjectToItem((idxi, idxj)))
                _key = self._BuildKey((idxi, idxj))
                children.append(self.ObjectToItem(_key))
            return len(self.fileTree.items[idxi].childs)
        elif len(objs) == 2:
            pass
        else:
            pass
        print("#################################################", len(objs), objs)
        return 0

        # ################### OK1
        # if not parent.IsOk():  # 根节点
        #     # print("##########################3 no ok")
        #     for idx, mu in enumerate(self.fileTree.items):
        #         children.append(self.ObjectToItem(idx))
        #         # print("GetChildren", "root", idx, mu.parent.fileName)
        #     return len(self.fileTree.items)
        
        # objs = self.ItemToObject(parent)
        # # print("GetChildren", objs)
        # for idxj, fl in enumerate(self.fileTree.items[objs].childs):
        #     children.append(self.ObjectToItem((objs, idxj)))
        # return len(self.fileTree.items[objs].childs)
    
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

        # objs = self.ItemToObject(item)        
        # # 父节点(部门)显示完整三列数据
        # if isinstance(objs, int):
        #     idxi = objs
        #     if col == 0:
        #         return f"{idxi+1}"
        #     elif col == 1:
        #         return self.fileTree.items[idxi].parent.fileName
        #     elif col == 2:
        #         return self.fileTree.items[idxi].parent.fileSize
        #     elif col == 3:
        #         return self.fileTree.items[idxi].parent.modifyAt
        #     else:
        #         return f"{idxi+1}"
        # # 子节点(项目)只显示名称，其他列显示占位符
        # elif isinstance(objs, tuple):
        #     idxi = objs[0]
        #     idxj = objs[1]
        #     if col == 0:
        #         return f"{idxi+1}.{idxj+1}"
        #     elif col == 1:
        #         return self.fileTree.items[idxi].childs[idxj].fileName
        #     elif col == 2:
        #         return self.fileTree.items[idxi].childs[idxj].fileSize
        #     elif col == 3:
        #         return self.fileTree.items[idxi].childs[idxj].modifyAt
        #     else:
        #         return f"{idxi+1}.{idxj+1}"
        # return "-"
    
    # 父类函数
    def GetParent(self, item):
        '''建立节点的反向链接'''
        if not item.IsOk():
            return dv.NullDataViewItem
        
        # 使用 ObjectToItem 和 ItemToObject 在数据和视图项之间转换
        # objs = self.ItemToObject(item)
        keys = self.ItemToObject(item)
        objs = self._ParseKey(keys)
        print("GetParent keys:", keys)
        if len(objs) == 1:
            return dv.NullDataViewItem
        elif len(objs) == 2:
            idxi = objs[0]
            key = self._BuildKey((idxi, ))
            return self.ObjectToItem(key)
        return dv.NullDataViewItem          # 部门的父节点是根

        # ################### OK1
        # objs = self.ItemToObject(item)
        # if isinstance(objs, tuple):  # 二级节点的父节点是一级节点
        #     return self.ObjectToItem(objs[0])
        # return dv.NullDataViewItem  # 一级节点的父节点是根
    
    # 展开使用
    def GetFirstChild(self, parent):
        """获取第一个子节点"""
        children = []
        count = self.GetChildren(parent, children)

        if parent.IsOk():
            print("GetFirstChild keys: IsOk", count, 1)
        else:
            print("GetFirstChild keys: IsOk not", count, 1)
        if count > 0:
            # print("GetFirstChild 444444444444444444444", count, 0)
            return (children[0], 1)  # 返回(子项, cookie)
        return (dv.NullDataViewItem, 0)
    
    # 展开使用
    def GetNextChild(self, item, cookie):
        """获取下一个子节点"""
        children = []
        count = self.GetChildren(item, children)

        # keys = self.ItemToObject(item)
        # print("GetNextChild keys:", keys)
        # # objs = self._ParseKey(keys)
        # # print("GetFirstChild keys:", keys)
        if item.IsOk():
            print("GetFirstChild keys: IsOk", count, cookie+1)
        else:
            print("GetFirstChild keys: IsOk not", count, cookie+1)

        if cookie < count:
            # print("GetNextChild 444444444444444444444", count, cookie)
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
        # self.dvc = dv.DataViewCtrl(panel, style=dv.DV_VARIABLE_LINE_HEIGHT|dv.DV_ROW_LINES)
        # # self.dvc = dv.DataViewCtrl(panel, style=wx.BORDER_THEME|dv.DV_ROW_LINES|dv.DV_VERT_RULES)
        
        # 创建并关联模型
        self.model = MultiColumnTreeModel()
        self.dvc.AssociateModel(self.model)
        self.model.DecRef()  # 避免内存泄漏

        # self._expand_all()
        self.on_expand_all()

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
        
        # # 添加搜索框
        # search_box = wx.SearchCtrl(panel)
        # search_box.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.on_search)
        # search_box.Bind(wx.EVT_TEXT, self.on_search_text)
        
        # 布局
        # sizer.Add(search_box, 0, wx.EXPAND|wx.ALL, 5)
        sizer.Add(self.dvc, 1, wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM, 5)
        panel.SetSizer(sizer)
        self.dvc.Refresh()     # 强制刷新界面
        self.dvc.Update()      # 确保 UI 更新
        
        # # 默认展开第一级
        # # root = self.dvc.GetTopItem()
        # root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        # # self.dvc.Expand(root)
        # child, cookie = self.model.GetFirstChild(root)
        # while child.IsOk():
        #     # objs = self.model.ItemToObject(child)
        #     # print("55555555555555555555", cookie, objs)
        #     self.dvc.Expand(root)
        #     child, cookie = self.model.GetNextChild(root, cookie)
        self.Center()

    def on_expand_all(self):
        """展开所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, True)
    
    def on_collapse_all(self):
        """折叠所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, False)
    
    def _RecursiveExpand(self, item, expand):
        """递归展开/折叠"""
        child, cookie = self.model.GetFirstChild(item)
        while child.IsOk():            
            if self.model.IsContainer(child):
                keys = self.model.ItemToObject(child)
                print("_RecursiveExpand keys:", keys, child.GetID())
                print("############################################ OK", expand)
                self.dvc.Expand(child) if expand else self.dvc.Collapse(child)
            else:
                print("############################################ MO", expand)
                self._RecursiveExpand(child, expand)
            child, cookie = self.model.GetNextChild(item, cookie)

    def on_search(self, event):
        """搜索按钮事件"""
        self.search_items(event.GetString())
    
    def on_search_text(self, event):
        """搜索文本变化事件"""
        self.search_items(event.GetString())
    
    def search_items(self, text):
        """搜索匹配项"""
        if not text:
            return
        
        root = self.dvc.GetTopItem()
        found_item = self.find_item(root, text.lower())
        
        if found_item.IsOk():
            self.dvc.Select(found_item)
            self.dvc.EnsureVisible(found_item)
    
    def find_item(self, parent, search_text):
        """递归查找匹配项"""
        child, cookie = self.dvc.GetFirstChild(parent)
        while child.IsOk():
            # 检查当前项
            for col in range(self.model.GetColumnCount()):
                value = self.model.GetValue(child, col).lower()
                if search_text in value:
                    return child
            
            # 如果是容器，递归检查子项
            if self.model.IsContainer(child):
                found_in_child = self.find_item(child, search_text)
                if found_in_child.IsOk():
                    return found_in_child
            
            child, cookie = self.dvc.GetNextChild(parent, cookie)
        
        return dv.NullDataViewItem

if __name__ == "__main__":
    app = wx.App()
    frame = MultiColumnTreeFrame(title="TreeDataModel")
    frame.Show()
    app.MainLoop()