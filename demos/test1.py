import wx
import wx.dataview as dv

class EditableModel(dv.PyDataViewModel):
    def __init__(self):
        dv.PyDataViewModel.__init__(self)
        self.data = ["苹果", "香蕉", "橙子"]
        
    def GetValue(self, item, col):
        obj = self.ItemToObject(item)
        # return dv.Variant(obj)
        print("GetValue", obj)
        return obj
        
    def SetValue(self, value, item, col):
        index = self.ItemToRow(item)
        print("SetValue", index)
        if 0 <= index < len(self.data):
            # self.data[index] = value.GetString()

            print("SetValue ##########1", self.data)
            print("SetValue ##########", index, value)
            self.data[index] = value
            # self.ValueChanged(item, col)
            self.ItemChanged(item)
            print("SetValue ##########2", self.data)
            return True
        return False
        
    # 其他必要方法...
    def GetChildren(self, parent, children):
        if not parent.IsOk():
            for item in self.data:
                children.append(self.ObjectToItem(item))
            return len(self.data)
        return 0
    
    def GetParent(self, item):
        """获取父节点"""
        if not item.IsOk():
            return dv.NullDataViewItem
        
        # obj = self.ItemToObject(item)
        # if isinstance(obj, tuple):  # 二级节点的父节点是一级节点
        #     return self.ObjectToItem(obj[0])
        return dv.NullDataViewItem  # 一级节点的父节点是根

    def IsContainer(self, item):
        return not item.IsOk()
        
    def GetColumnCount(self):
        return 1
        
    # def ObjectToItem(self, obj):
    #     return dv.DataViewItem(obj)
        
    # def ItemToObject(self, item):
    #     return item.GetID()
        
    def ItemToRow(self, item):
        try:
            # row = self.dataViewCtrl.GetModel().GetRow(item)
            return self.data.index(self.ItemToObject(item))
        except ValueError:
            return -1

class MyFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="可编辑DataViewCtrl", size=(400, 300))
        
        # 创建控件
        self.dvc = dv.DataViewCtrl(self, style=dv.DV_ROW_LINES|dv.DV_VERT_RULES)
        
        # # 创建可编辑列
        # col = dv.DataViewColumn("水果", 0, width=200, mode=dv.DATAVIEW_CELL_EDITABLE)
        # self.dvc.AppendColumn(col)
        
        self.dvc.AppendTextColumn("分类", 0, width=200, mode=dv.DATAVIEW_CELL_EDITABLE)

        # 设置模型
        self.model = EditableModel()
        self.dvc.AssociateModel(self.model)
        self.model.DecRef()
        
        # 添加修改按钮
        btn = wx.Button(self, label="修改选中项")
        btn.Bind(wx.EVT_BUTTON, self.on_edit)
        
        # 布局
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.dvc, 1, wx.EXPAND|wx.ALL, 5)
        sizer.Add(btn, 0, wx.ALIGN_CENTER|wx.ALL, 5)
        self.SetSizer(sizer)
    
    def on_edit(self, event):
        item = self.dvc.GetSelection()
        print("Onedit", item)
        if item.IsOk():
            # # 弹出对话框输入新值
            # dlg = wx.TextEntryDialog(self, "输入新值:", "修改内容")
            # if dlg.ShowModal() == wx.ID_OK:
            #     new_value = dlg.GetValue()
            #     # self.model.SetValue(dv.Variant(new_value), item, 0)
            #     self.model.SetValue(new_value, item, 0)
            # dlg.Destroy()

            # print("OnEdit", )
            self.model.SetValue("---", item, 0)
            # self.model.ValueChanged(item, 0)

            # self.dvc.Refresh()

app = wx.App()
frame = MyFrame()
frame.Show()
app.MainLoop()