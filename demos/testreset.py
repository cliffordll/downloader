import wx
import wx.dataview as dv

class MyDataModel(dv.PyDataViewModel):
    def __init__(self):
        dv.PyDataViewModel.__init__(self)
        self.data = [
            {"id": 1, "name": "苹果", "price": 5.99},
            {"id": 2, "name": "香蕉", "price": 3.49},
            {"id": 3, "name": "橙子", "price": 4.29}
        ]
    
    def IsContainer(self, item):
        return not item.IsOk()

    def GetChildren(self, parent, children):
        if not parent:
            for item in self.data:
                children.append(self.ObjectToItem(item))
            return len(self.data)
        return 0
    
    def GetValue(self, item, col):
        data = self.ItemToObject(item)
        if col == 0:
            return str(data["id"])
        elif col == 1:
            return data["name"]
        elif col == 2:
            return f"${data['price']:.2f}"
        return ""
    
    def GetColumnCount(self):
        return 3

class MainFrame(wx.Frame):
    def __init__(self):
        wx.Frame.__init__(self, None, title="批量更新示例", size=(500, 300))
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 创建控件
        self.dvc = dv.DataViewCtrl(panel, style=wx.BORDER_THEME|dv.DV_ROW_LINES)
        self.model = MyDataModel()
        self.dvc.AssociateModel(self.model)
        # 添加列
        self.dvc.AppendTextColumn("ID", 0, width=50)
        self.dvc.AppendTextColumn("名称", 1, width=150)
        self.dvc.AppendTextColumn("价格", 2, width=100)
        
        # 添加按钮
        btn_update = wx.Button(panel, label="批量更新数据")
        btn_update.Bind(wx.EVT_BUTTON, self.on_bulk_update)
        
        # 布局
        sizer.Add(self.dvc, 1, wx.EXPAND|wx.ALL, 5)
        # sizer.Add(panel, 0, wx.EXPAND)
        sizer.Add(btn_update, 0, wx.ALL, 5)

        panel.SetSizer(sizer)
        # self.SetSizer(sizer)
    
    def on_bulk_update(self, event):
        """正确的批量更新方法"""
        # 1. 首先通知视图数据将被清除
        self.model.Cleared()
        
        # 2. 更新数据源
        self.model.data = [
            {"id": 10, "name": "西瓜", "price": 12.99},
            {"id": 20, "name": "芒果", "price": 8.50},
            {"id": 30, "name": "葡萄", "price": 6.99},
            {"id": 40, "name": "菠萝", "price": 9.25}
        ]
        
        # 3. 通知视图数据已改变
        # self.model.ValueChanged()

if __name__ == "__main__":
    app = wx.App()
    frame = MainFrame()
    frame.Show()
    app.MainLoop()