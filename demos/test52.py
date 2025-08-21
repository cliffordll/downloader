import wx
import wx.dataview as dv

class TreeDataModel(dv.PyDataViewModel):
    def __init__(self, data):
        dv.PyDataViewModel.__init__(self)
        self.data = data  # 树形结构数据
    
    # 必须实现的抽象方法
    def GetColumnCount(self):
        return 1  # 单列树
    
    def GetColumnType(self, col):
        return "string"
    
    def GetChildren(self, parent, children):
        """返回父节点的子节点"""
        if not parent.IsOk():  # 根节点
            for key in self.data.keys():
                children.append(self.ObjectToItem(key))
            return len(self.data)
        else:
            parent_obj = self.ItemToObject(parent)
            if parent_obj in self.data:  # 一级节点
                for idx, child in enumerate(self.data[parent_obj]):
                    children.append(self.ObjectToItem((parent_obj, idx)))
                return len(self.data[parent_obj])
        return 0

        # if not parent.IsOk():  # 根节点
        #     for key in self.data.keys():
        #         children.append(self.ObjectToItem((key,)))
        #         # children.append(self.ObjectToItem(key))
        #         print("#########################1", key)
        #     return len(self.data)
        # else:
        #     objs = self.ItemToObject(parent)

        #     parent_obj = objs[0]
        #     print("#########################2", parent_obj, objs)
        #     if parent_obj in self.data:  # 一级节点
        #         for child in self.data[parent_obj]:
        #             children.append(self.ObjectToItem((parent_obj, child)))
        #         return len(self.data[parent_obj])
        # return 0
    
    def IsContainer(self, item):
        """判断节点是否包含子节点"""
        if not item.IsOk():  # 根节点是容器
            return True
        
        obj = self.ItemToObject(item)
        if isinstance(obj, tuple):  # 二级节点不是容器
            return False
        return True  # 一级节点是容器

        # objs = self.ItemToObject(item)
        # if len(objs) == 2:  # 二级节点不是容器
        #     return False
        # return True         # 一级节点是容器
    
    def GetParent(self, item):
        """获取父节点"""
        if not item.IsOk():
            return dv.NullDataViewItem
        
        obj = self.ItemToObject(item)
        if isinstance(obj, tuple):  # 二级节点的父节点是一级节点
            return self.ObjectToItem(obj[0])
        return dv.NullDataViewItem  # 一级节点的父节点是根

        # objs = self.ItemToObject(item)
        # if len(objs) == 2:
        # # if isinstance(obj, tuple):  # 二级节点的父节点是一级节点
        #     return self.ObjectToItem(objs[0])
        # return dv.NullDataViewItem  # 一级节点的父节点是根

    def GetValue(self, item, col):
        """获取显示值"""
        obj = self.ItemToObject(item)
        if isinstance(obj, tuple):  # 二级节点
            print(obj[0])
            print(obj[1])
            # return obj[1]
            return self.data[obj[0]][obj[1]]
        return str(obj)  # 一级节点

        # objs = self.ItemToObject(item)
        # if len(objs) == 2:  # 二级节点
        #     return objs[1]
        # return str(objs[0])  # 一级节点

    def SetValue(self, variant, item, col):
        print(variant, col)
        obj = self.ItemToObject(item)
        # if col == 0:
        if isinstance(obj, tuple):  # 二级节点
            print("SetValue", obj, variant)

            # self.data.update(id, variant)
            key = obj[0]
            self.data[key][0] = variant

            print("SetValue", self.data)
            # self.ValueChanged(item, col)
            self.ItemChanged(item)
            return True
        return False
    
    #  def SetValue(self, value, item, col):
    #     index = self.ItemToRow(item)
    #     if 0 <= index < len(self.data):
    #         self.data[index] = value.GetString()
    #         self.ValueChanged(item, col)
    #         return True
    #     return False
    

      # 动态插入数据的方法
    def AddData(self, new_data):
        for key in new_data.keys():
            if key not in self.data.keys():
                self.data.update(new_data)
                self.ItemAdded(dv.NullDataViewItem, self.ObjectToItem(key))

    def AddChildData(self, key, data: list):
        if key in self.data.keys():
            self.data[key] += data
            print(self.data[key])
            parent = self.ObjectToItem(key)

            for child in data:
                self.ItemAdded(parent, self.ObjectToItem(child))

    # 展开使用
    def GetFirstChild(self, parent):
        """获取第一个子节点"""
        children = []
        count = self.GetChildren(parent, children)
        if count > 0:
            print("GetFirstChild 444444444444444444444", count, 0)
            return (children[0], 1)  # 返回(子项, cookie)
        return (dv.NullDataViewItem, 0)
    
    # 展开使用
    def GetNextChild(self, item, cookie):
        """获取下一个子节点"""
        children = []
        count = self.GetChildren(item, children)
        if cookie < count:
            print("GetNextChild 444444444444444444444", count, cookie)
            return (children[cookie], cookie+1)
        return (dv.NullDataViewItem, 0)
    
class TreeDataFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="树状结构示例", size=(400, 400))
        
        # 准备树形数据
        tree_data = {
            "电子产品": ["手机", "电脑", "平板"],
            "服装": ["上衣", "裤子", "鞋子"]
        }
        
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 创建DataViewCtrl
        self.dvc = dv.DataViewCtrl(
            panel, 
            style=wx.BORDER_THEME | dv.DV_ROW_LINES | dv.DV_VERT_RULES
        )
        

        # 创建并关联模型
        self.model = TreeDataModel(tree_data)
        self.dvc.AssociateModel(self.model)
        
        # 添加列
        self.dvc.AppendTextColumn("分类", 0, width=200, mode=dv.DATAVIEW_CELL_EDITABLE)
        
        # 添加展开/折叠按钮
        self.expand_btn = wx.Button(panel, label="展开全部")
        self.collapse_btn = wx.Button(panel, label="折叠全部")
        self.refresh_btn = wx.Button(panel, label="刷新")
        self.change_btn = wx.Button(panel, label="修改")
        
        # 按钮事件
        self.expand_btn.Bind(wx.EVT_BUTTON, self.on_expand_all)
        self.collapse_btn.Bind(wx.EVT_BUTTON, self.on_collapse_all)
        self.refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh_all)
        self.change_btn.Bind(wx.EVT_BUTTON, self.on_change_all)
        
        # 布局
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(self.expand_btn, 0, wx.RIGHT, 5)
        btn_sizer.Add(self.collapse_btn, 0)
        btn_sizer.Add(self.refresh_btn, 0)
        btn_sizer.Add(self.change_btn, 0)
        
        sizer.Add(self.dvc, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.BOTTOM, 5)
        panel.SetSizer(sizer)
        
        # 绑定选择事件
        self.dvc.Bind(dv.EVT_DATAVIEW_SELECTION_CHANGED, self.on_selection_changed)
    
    def on_expand_all(self, event):
        """展开所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self.recursive_expand(root, True)
    
    def on_collapse_all(self, event):
        """折叠所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self.recursive_expand(root, False)

    def on_refresh_all(self, event):
        """折叠所有节点"""
        # new_data = {
        #     "电子产品": ["手机", "电脑", "平板"],
        #     "家具": ["沙发", "床", "桌子"],
        #     "服装": ["上衣", "裤子", "鞋子"]
        # }
        new_data = {
            "家具": ["沙发", "床", "桌子"]
        }

        self.model.AddData(new_data)  # 调用模型的方法插入数据

        # self.model.AddChildData("家具", ["椅子", "空调"])
        self.model.AddChildData("服装", ["椅子", "空调"])
        # # root = self.dvc.GetTopItem()
        # root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        # self.recursive_expand(root, False)

    def on_change_all(self, event):
        # self.ValueChanged(item, 0)    # 更新某列

        pass
    
    def recursive_expand(self, item, expand):
        """递归展开/折叠"""
        if self.model.IsContainer(item):
            self.dvc.Expand(item) if expand else self.dvc.Collapse(item)
            child, cookie = self.model.GetFirstChild(item)
            while child.IsOk():
                self.recursive_expand(child, expand)
                child, cookie = self.model.GetNextChild(item, cookie)
    
    def on_selection_changed(self, event):
        """选中项变化事件"""
        item = event.GetItem()
        if item.IsOk():
            value = self.model.GetValue(item, 0)
            wx.MessageBox(f"你选择了: {value}", "提示")

            # self.model.SetValue(dv.Variant(new_value), item, 0)
            self.model.SetValue("----", item, 0)
            # self.dvc.Refresh()
            # self.model.SetValue(dv.Variant("---"), item, 0)


if __name__ == "__main__":

    app = wx.App()
    frame = TreeDataFrame()
    frame.Show()
    app.MainLoop()