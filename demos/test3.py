import wx
import wx.dataview as dv

class MyModel(dv.PyDataViewIndexListModel):
    def __init__(self, data):
        dv.PyDataViewIndexListModel.__init__(self, len(data))
        self.data = data
    
    def GetColumnType(self, col):
        return "string"
    
    def GetValueByRow(self, row, col):
        print("GetValueByRow", row, col)
        return self.data[row][col]
    
    def SetValueByRow(self, value, row, col):
        print("SetValueByRow", row, col, value)
        self.data[row][col] = value
        return True
    
    def GetColumnCount(self):
        return len(self.data[0]) if self.data else 0

class MyFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="自定义模型修改示例")
        
        # 准备数据
        data = [
            ["101", "笔记本电脑", "5999"],
            ["102", "智能手机", "3999"],
            ["103", "平板电脑", "2599"]
        ]
        
        # 创建模型和视图
        self.model = MyModel(data)
        self.dvc = dv.DataViewCtrl(self, style=wx.BORDER_THEME|dv.DV_ROW_LINES)
        self.dvc.AssociateModel(self.model)
        
        # 添加列
        self.dvc.AppendTextColumn("ID", 0, width=60)
        self.dvc.AppendTextColumn("名称", 1, width=120)
        self.dvc.AppendTextColumn("价格", 2, width=80)
        
        # 修改按钮
        btn = wx.Button(self, label="修改价格")
        btn.Bind(wx.EVT_BUTTON, self.on_modify)
        
        # 布局
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.dvc, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        sizer.Add(btn, flag=wx.ALIGN_CENTER|wx.ALL, border=5)
        self.SetSizer(sizer)
        self.SetSize(400, 300)
    
    def on_modify(self, event):
        item = self.dvc.GetSelection()
        if not item.IsOk():
            wx.MessageBox("请先选择一项", "提示", wx.OK|wx.ICON_INFORMATION)
            return
        
        row = self.model.GetRow(item)
        print("on_modify", row)
        
        # 修改价格(第三列)
        self.model.SetValueByRow("9999", row, 2)
        
        # 通知视图更新
        # self.model.ValueChanged(row, 2)
        self.model.ValueChanged(item, 2)

app = wx.App()
frame = MyFrame()
frame.Show()
app.MainLoop()