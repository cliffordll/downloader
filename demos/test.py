import wx
import wx.dataview as dv

class TreeListFrame(wx.Frame):
    def __init__(self, parent):
        super().__init__(parent, title="TreeListCtrl 示例", size=(500, 400))
        
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 创建 TreeListCtrl
        self.tree = dv.TreeListCtrl(panel, style=dv.TL_MULTIPLE|dv.DV_ROW_LINES)
        # self.tree = dv.TreeListCtrl(panel, style=dv.TL_MULTIPLE)
        self.tree.AppendColumn("名称", width=200)
        self.tree.AppendColumn("类型", width=100)
        
        # 添加根节点
        self.root = self.tree.AppendItem(dv.NullDataViewItem, "项目根目录")
        # self.root = self.tree.AppendContainer(dv.NullDataViewItem, "Root")
        # self.root = self.tree.AppendItem(dv.ro, "项目根目录")

        # 添加示例数据
        self.populate_tree()
        
        # 按钮
        btn_add = wx.Button(panel, label="添加子项")
        btn_delete = wx.Button(panel, label="删除选中")
        
        # 布局
        sizer.Add(self.tree, 1, wx.EXPAND|wx.ALL, 5)
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(btn_add, 0, wx.RIGHT, 5)
        btn_sizer.Add(btn_delete, 0)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER|wx.ALL, 5)
        panel.SetSizer(sizer)
        
        # 绑定事件
        btn_add.Bind(wx.EVT_BUTTON, self.on_add_item)
        btn_delete.Bind(wx.EVT_BUTTON, self.on_delete_item)
        self.tree.Bind(dv.EVT_TREELIST_ITEM_ACTIVATED, self.on_item_activated)
        
        self.Show()
    
    def populate_tree(self):
        """填充示例数据"""
        # 添加文件夹
        folder1 = self.tree.AppendItem(self.root, "重要文档")
        self.tree.SetItemText(folder1, "文件夹", 1)
        
        # 添加文件
        file1 = self.tree.AppendItem(folder1, "报告.docx")
        self.tree.SetItemText(file1, "Word文档", 1)
        
        file2 = self.tree.AppendItem(folder1, "预算.xlsx")
        self.tree.SetItemText(file2, "Excel文件", 1)
        
        # 展开
        self.tree.Expand(self.root)
        self.tree.Expand(folder1)
    
    def on_add_item(self, event):
        """添加子项"""
        item = self.tree.GetSelection()
        if not item.IsOk():
            item = self.root  # 默认添加到根节点
        
        new_item = self.tree.AppendItem(item, "新项目")
        self.tree.SetItemText(new_item, "新类型", 1)
        self.tree.Expand(item)
    
    def on_delete_item(self, event):
        """删除选中项"""
        item = self.tree.GetSelection()
        if item.IsOk() and item != self.root:  # 防止删除根节点
            parent = self.tree.GetItemParent(item)
            self.tree.DeleteItem(item)
            if parent.IsOk():
                self.tree.Expand(parent)  # 刷新父节点
    
    def on_item_activated(self, event):
        """双击项目事件"""
        item = event.GetItem()
        if item.IsOk():
            name = self.tree.GetItemText(item, 0)
            wx.MessageBox(f"已激活: {name}", "提示")

if __name__ == "__main__":
    # app = wx.App()
    # frame = TreeListFrame(None)
    # app.MainLoop()

    # keys = "0.0"
    keys = "0"

    def _BuildKey(keys: tuple):
        # return ".".join(keys)
        return ".".join(map(str, keys))
    
    def _ParseKey(keyStr: str):
        # return keyStr.split('.')
        return tuple(map(int, keyStr.split(".")))

    key1 = _BuildKey((10,))
    print(type(key1), key1, id(key1))
    # keys = "0"
    keys = _ParseKey(key1)
    print(keys)
    key2 = _BuildKey(keys)
    print(type(key2), key2, id(key2))

    key_map = dict()
    print(key_map)
    key_map[key1] = key1

    key1_1 = key_map.get(key1)
    print(id(key_map.get(key1)))
    print(id(key1_1))
    
    if key2 not in key_map.keys():
        key_map[key2] = key2
    key2_1 = key_map.get(key2)
    print(id(key_map.get(key2)))
    print(id(key2_1))