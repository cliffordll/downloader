import wx
import wx.dataview as dv

class ProductModel(dv.PyDataViewModel):
    def __init__(self):
        dv.PyDataViewModel.__init__(self)
        # 示例数据 - 实际应用中可能从数据库或其他来源获取
        self.products = [
            {"id": 1, "name": "笔记本电脑", "price": 5999, "stock": 10},
            {"id": 2, "name": "智能手机", "price": 3999, "stock": 15},
            {"id": 3, "name": "平板电脑", "price": 2599, "stock": 8}
        ]
        
        # # 建立数据项到内部数据的映射
        # self.item_map = {}
        # for i, product in enumerate(self.products):
        #     self.item_map[self.ObjectToItem(i)] = product
    
    def GetColumnCount(self):
        return 4  # id, name, price, stock
    
    def GetColumnType(self, col):
        # 定义每列的数据类型
        if col == 0:  # ID列
            return "long"
        elif col == 3:  # 库存列
            return "long"
        else:
            return "string"
    
    def GetChildren(self, parent, children):
        if not parent:
            # 根节点，返回所有产品
            for i in range(len(self.products)):
                children.append(self.ObjectToItem(i))
            return len(self.products)
        return 0
    
    def GetParent(self, item):
        return dv.NullDataViewItem  # 我们使用平面列表，没有层级结构
    
    def IsContainer(self, item):
        return not item  # 只有根节点是容器
    
    def GetValue(self, item, col):
        # 获取数据项的值
        if not item:
            return ""
        
        objs = self.ItemToObject(item)
        # print(objs)
        
        # product = self.item_map.get(item, None)
        # print("GetValue", objs, product)
        # if not product:
        #     return ""

        product = self.products[objs] 
        
        if col == 0:
            return str(product["id"])
        elif col == 1:
            return product["name"]
        elif col == 2:
            return f"¥{product['price']:.2f}"
        elif col == 3:
            return str(product["stock"])
        return ""
    
    def SetValue(self, value, item, col):
        # 修改数据项的值
        # product = self.item_map.get(item, None)
        # if not product:
        #     return False

        objs = self.ItemToObject(item)
        product = self.products[objs] 
        
        try:
            print("1111", self.products)
            if col == 1:  # 名称
                product["name"] = value
            elif col == 2:  # 价格
                # 去除货币符号并转换为浮点数
                price_str = value.replace("¥", "").strip()
                product["price"] = float(price_str)
            elif col == 3:  # 库存
                product["stock"] = int(value)
            else:
                return False  # ID列不允许修改
            
            print("2222", self.products)
            # 通知视图更新
            self.ItemChanged(item)
            return True
        except (ValueError, TypeError):
            return False
    
    # def GetAttr(self, item, col, attr):
    #     # 可选：设置某些项的显示属性
    #     if col == 3:  # 库存列
    #         product = self.item_map.get(item, None)
    #         if product and product["stock"] < 5:
    #             attr.Colour = wx.RED
    #             attr.Bold = True
    #             return True
    #     return False

class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="PyDataViewModel 修改示例", size=(600, 400))
        
        # 创建数据视图控件
        self.dvc = dv.DataViewCtrl(self, style=wx.BORDER_THEME|dv.DV_ROW_LINES)
        
        # 创建并关联模型
        self.model = ProductModel()
        self.dvc.AssociateModel(self.model)
        
        # 添加列
        self.dvc.AppendTextColumn("ID", 0, width=50)
        self.dvc.AppendTextColumn("商品名称", 1, width=150, mode=dv.DATAVIEW_CELL_EDITABLE)
        # price_col = dv.DataViewColumn("价格", 
        #                             dv.DataViewTextRenderer(), 
        #                             2, 
        #                             width=100,
        #                             mode=dv.DATAVIEW_CELL_EDITABLE)
        # self.dvc.AppendColumn(price_col)
        self.dvc.AppendTextColumn("价格", 2, width=100, mode=dv.DATAVIEW_CELL_EDITABLE)
        self.dvc.AppendTextColumn("库存", 3, width=80, mode=dv.DATAVIEW_CELL_EDITABLE)
        
        # 添加修改按钮
        panel = wx.Panel(self)
        btn_modify = wx.Button(panel, label="修改选中商品价格")
        btn_modify.Bind(wx.EVT_BUTTON, self.on_modify_price)
        
        # 布局
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.dvc, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(btn_modify, flag=wx.ALL, border=5)
        panel.SetSizer(btn_sizer)
        
        sizer.Add(panel, flag=wx.EXPAND|wx.ALL, border=5)
        self.SetSizer(sizer)
        
        self.Centre()
    
    def on_modify_price(self, event):
        item = self.dvc.GetSelection()
        if not item.IsOk():
            wx.MessageBox("请先选择一项", "提示", wx.OK|wx.ICON_INFORMATION)
            return
        
        # obj = self.model.ItemToObject(item)
        # print(obj)
        
        # current_price = self.model.GetValue(item, 2)
        self.model.SetValue(f"121", item, 2)
        

        # # 获取当前价格
        # current_price = self.model.GetValue(item, 2)
        
        # # 弹出对话框获取新价格
        # dlg = wx.TextEntryDialog(self, "输入新价格:", "修改价格", current_price.replace("¥", ""))
        # if dlg.ShowModal() == wx.ID_OK:
        #     # new_price = dlg.GetValue()
        #     new_price = 111
        #     if self.model.SetValue(f"¥{new_price}", item, 2):
        #         wx.MessageBox("价格修改成功!", "成功", wx.OK|wx.ICON_INFORMATION)
        #     else:
        #         wx.MessageBox("价格修改失败! 请输入有效的数字", "错误", wx.OK|wx.ICON_ERROR)
        # dlg.Destroy()

if __name__ == "__main__":
    app = wx.App()
    frame = MainFrame()
    frame.Show()
    app.MainLoop()