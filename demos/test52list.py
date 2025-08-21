import wx
import wx.dataview as dv

class TreeDataModel(dv.PyDataViewModel):
    def __init__(self):
        dv.PyDataViewModel.__init__(self)
        self.data = [{"category":"电子产品","childs":["手机", "电脑", "平板"]},
                     {"category":"服装","childs":["上衣", "裤子", "鞋子"]},]
        # 因为 ObjectToItem(obj) 在库内部维护一张map，key 为 id(obj)，所以 obj 对象不能变
        # id(obj) 函数返回对象的"标识值"
        self.keyMap = dict()
    
    # 必须实现的抽象方法
    def GetColumnCount(self):
        return 1  # 单列树
    
    # 父类函数（父节点显示多列）
    def HasContainerColumns(self, item):
        return True
    
    def GetColumnType(self, col):
        return "string"
    
    def _BuildKey(self, keys: tuple):
        _key = ".".join(map(str, keys))
        # 维护 obj 内存地址不变
        if _key not in self.keyMap.keys():
            self.keyMap[_key] = _key
        return self.keyMap[_key]
    
    def _ParseKey(self, keyStr: str):
        return tuple(map(int, keyStr.split(".")))
    
    def GetChildren(self, parent, children):
        """返回父节点的子节点"""
        if not parent.IsOk():  # 根节点
            for idx, val in enumerate(self.data):
                # children.append(self.ObjectToItem(key))
                _key = self._BuildKey((idx,))
                children.append(self.ObjectToItem(_key))
            return len(self.data)
        
        keys = self.ItemToObject(parent)
        objs = self._ParseKey(keys)
        idxi = objs[0]      # 一级节点
        for idxj, valj in enumerate(self.data[idxi]["childs"]):
            _key = self._BuildKey((idxi, idxj))
            children.append(self.ObjectToItem(_key))
        return len(self.data[idxi]["childs"])
    
    def IsContainer(self, item):
        """判断节点是否包含子节点"""
        if not item.IsOk():
            return True
        
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
            return self.ObjectToItem(_key)
        return dv.NullDataViewItem          # 部门的父节点是根

    def GetValue(self, item, col):
        '''提供节点数据显示'''
        keys = self.ItemToObject(item)
        objs = self._ParseKey(keys)
        # print("GetValue keys:", keys)
        if len(objs) == 1:
            idxi = objs[0]
            if col == 0:
                return self.data[idxi]["category"]
            else:
                return f"{idxi+1}"
        elif len(objs) == 2:
            idxi = objs[0]
            idxj = objs[1]
            if col == 0:
                return self.data[idxi]["childs"][idxj]
            else:
                return f"{idxi+1}.{idxj+1}"
        else:
            return "----"
        
    def SetValue(self, variant, item, col):
        keys = self.ItemToObject(item)
        objs = self._ParseKey(keys)
        if len(objs) == 1:
            idxi = objs[0]
            self.data[idxi]["category"] = variant
            return True
        elif len(objs) == 2:
            idxi = objs[0]
            idxj = objs[1]
            self.data[idxi]["childs"][idxj] = variant
            return True
        return False

    # 动态插入数据的方法
    def AddData(self, data: list):
        for item in data:
            idxi = len(self.data)
            self.data.append(item)
            _key = self._BuildKey((idxi,))
            parent = self.ObjectToItem(_key)
            self.ItemAdded(dv.NullDataViewItem, parent)
            # self.ItemAdded(dv.NullDataViewItem, self.ObjectToItem(_key))

            for child in item["childs"]:
                self.ItemAdded(parent, self.ObjectToItem(child))

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
    
class TreeDataFrame(wx.Frame):
    def __init__(self, title):
        super().__init__(None, size=(400, 400))
        self.SetTitle(title)
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 创建DataViewCtrl
        self.dvc = dv.DataViewCtrl(
            panel, 
            style=wx.BORDER_THEME | dv.DV_ROW_LINES | dv.DV_VERT_RULES
        )
        
        # 创建并关联模型
        self.model = TreeDataModel()
        self.dvc.AssociateModel(self.model)
        # 添加列
        self.dvc.AppendTextColumn("分类", 0, width=200, mode=dv.DATAVIEW_CELL_EDITABLE)
        self.dvc.AppendTextColumn("索引", 1, width=180)
        
        # 添加展开/折叠按钮
        self.expand_btn = wx.Button(panel, label="展开全部")
        self.collapse_btn = wx.Button(panel, label="折叠全部")
        self.refresh_btn = wx.Button(panel, label="添加")
        self.change_btn = wx.Button(panel, label="修改")
        # 按钮事件
        self.expand_btn.Bind(wx.EVT_BUTTON, self.on_expand_all)
        self.collapse_btn.Bind(wx.EVT_BUTTON, self.on_collapse_all)
        self.refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh_all)
        self.change_btn.Bind(wx.EVT_BUTTON, self.on_change_all)
        self.on_expand_all(None)
        
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
        
    def _RecursiveExpand(self, item=dv.NullDataViewItem, expand=True):
        """递归展开/折叠"""
        if self.model.IsContainer(item):
            self.dvc.Expand(item) if expand else self.dvc.Collapse(item)
            child, cookie = self.model.GetFirstChild(item)
            while child.IsOk():
                self._RecursiveExpand(child, expand)
                child, cookie = self.model.GetNextChild(item, cookie)
    
    def on_expand_all(self, event):
        """展开所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, True)
    
    def on_collapse_all(self, event):
        """折叠所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, False)

    def on_refresh_all(self, event):
        """折叠所有节点"""
        new_data = [{"category":"家具","childs":["椅子", "桌子"]}]
        self.model.AddData(new_data)  # 调用模型的方法插入数据

        # root = self.dvc.GetTopItem()
        # root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand()

    def on_change_all(self, event):
        # self.ValueChanged(item, 0)    # 更新某列
        wx.MessageBox(f"选择某项直接可以直接修改", "提示")

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
    frame = TreeDataFrame("TreeDataModel")
    frame.Show()
    app.MainLoop()