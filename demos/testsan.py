import wx
import wx.dataview as dv

class ThreeLevelModel(dv.PyDataViewModel):
    def __init__(self, data):
        dv.PyDataViewModel.__init__(self)
        self.data = data
        
    # 判断是否是容器节点（是否可以展开）
    def IsContainer(self, item):
        if not item.IsOk():  # 根节点是容器
            return True
            
        obj = self.ItemToObject(item)
        
        # 如果是字符串，检查它是否是一级节点（字典键）
        if isinstance(obj, str):
            return obj in self.data or any(obj in v for v in self.data.values())
            
        return False
    
    # 获取节点的子节点
    def GetChildren(self, parent, children):
        if not parent.IsOk():  # 根节点
            for root_name in self.data.keys():
                children.append(self.ObjectToItem(root_name))
            return len(self.data)
            
        obj = self.ItemToObject(parent)
        
        if obj in self.data:  # 根节点 → 一级节点
            for level1 in self.data[obj].keys():
                children.append(self.ObjectToItem(level1))
            return len(self.data[obj])
            
        # 检查是否是一级节点 → 二级节点
        for root_name, level1_nodes in self.data.items():
            if obj in level1_nodes:
                for level2 in level1_nodes[obj]:
                    children.append(self.ObjectToItem(level2))
                return len(level1_nodes[obj])
                
        return 0
    
    # 获取节点显示的值
    def GetValue(self, item, col):
        obj = self.ItemToObject(item)
        # return dv.Variant(str(obj))
        return "---"
    
    # # 辅助方法：将Python对象转换为DataViewItem
    # def ObjectToItem(self, obj):
    #     return dv.DataViewItem(obj)
    
    # # 辅助方法：将DataViewItem转换为Python对象
    # def ItemToObject(self, item):
    #     return item.GetID()
    
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

    
class MyFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="三级节点示例")
        
        # 创建数据
        data = {
            "部门A": {
                "团队1": ["张三", "李四"],
                "团队2": ["王五", "赵六"]
            },
            "部门B": {
                "团队3": ["钱七", "孙八"]
            }
        }
        
        # 创建控件
        self.dvc = dv.DataViewCtrl(self, style=dv.DV_VERT_RULES|dv.DV_HORIZ_RULES)
        self.model = ThreeLevelModel(data)
        self.dvc.AssociateModel(self.model)
        self.model.DecRef()  # 避免内存泄漏
        
        # 添加列
        self.dvc.AppendTextColumn("组织架构", 0, width=200)
        
        # 默认展开所有根节点
        # wx.CallAfter(self.expand_all)
        wx.CallAfter(self.on_expand_all)
        
        self.Show()
    
    # def expand_all(self):
    #     model = self.dvc.GetModel()
    #     # root = model.GetRootItem()
    #     root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        
    #     # 展开所有根节点
    #     child, cookie = model.GetFirstChild(root)
    #     while child.IsOk():
    #         self.dvc.Expand(child)
            
    #         # 展开所有一级节点
    #         grandchild, grandcookie = model.GetFirstChild(child)
    #         while grandchild.IsOk():
    #             self.dvc.Expand(grandchild)
    #             grandchild, grandcookie = model.GetNextChild(child, grandcookie)
                
    #         child, cookie = model.GetNextChild(root, cookie)

    def on_expand_all(self):
        """展开所有节点"""
        # root = self.dvc.GetTopItem()
        # root = self.dvc.GetRootItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点

        # objs = self.model.ItemToObject(root)
        # print("###########################", objs)
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


app = wx.App()
frame = MyFrame()
app.MainLoop()