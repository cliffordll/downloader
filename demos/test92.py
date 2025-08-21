import wx
import wx.dataview as dv

class TreeDataModel(dv.PyDataViewModel):
    def __init__(self):
        dv.PyDataViewModel.__init__(self)
        # # 示例数据：部门(父节点)包含名称、经理、预算三列数据
        self.data = {
            "认识部": {
                "manager": "王主任",
                "budget": "30 万", 
                "children": {
                    "项目A": ["20 万", "张三"], 
                    "项目B": ["20 万", "李四"], 
                    "项目C": ["20 万", "王五"]
                }
            },            
            "市场部": {
                "manager": "李主管",
                "budget": "20 万", 
                "children": {
                    "项目X": ["20 万", "张三"], 
                    "项目Y": ["20 万", "李四"], 
                    "项目Z": ["20 万", "王五"]
                }
            }
        }

    def GetColumnCount(self):
        return 3  # 名称、负责人、预算三列

    # 父类函数（获取项目值）
    def GetValue(self, item, col):
        node = self.ItemToObject(item)        
        # 父节点(部门)显示完整三列数据
        if isinstance(node, str):
            if col == 0:  # 名称列
                return node
            elif col == 1:  # 负责人列
                return self.data[node]["manager"]
            elif col == 2:  # 预算列
                return self.data[node]["budget"]
        
        # 子节点(项目)只显示名称，其他列显示占位符
        elif isinstance(node, tuple):
            dept = node[0]
            if col == 0:
                return node[1]  # 项目名称
            # return "-"  # 其他列
            elif col == 1:  # 负责人列
                return self.data[dept]["children"][node[1]][1]
            elif col == 2:  # 预算列
                return self.data[dept]["children"][node[1]][0]
        return "----"
    
    # def SetValue(self, variant, item, col):
    #     print(variant, col)
    #     id = self.ItemToObject(item)
    #     # if col == 0:
    #     #     self.dao.update(id, variant)
    #     return True

    # 父类函数（获取子项目列表）
    # ObjectToItem ≈ 给你的数据对象分配一个"座位号"（DataViewItem）
    # ItemToObject ≈ 通过"座位号"找到实际的数据对象
    def GetChildren(self, parent, children):
        if not parent.IsOk():  # 根节点
            for dept in self.data:
                # 将数据对象转换为 DataViewItem 对象，这是 DataViewCtrl 内部使用的标识符
                children.append(self.ObjectToItem(dept))
            return len(self.data)
        
        dept = self.ItemToObject(parent)
        for project in self.data[dept]["children"].keys():
            # 根据项目名
            children.append(self.ObjectToItem((dept, project)))
        return len(self.data[dept]["children"].keys())
    
    # 父类函数（判断项目是否为容器（如分组节点））
    def IsContainer(self, item):
        if not item.IsOk():
            return True
        return isinstance(self.ItemToObject(item), str)  # 只有部门是可展开容器

    # 父类函数（是否展示父类 其余的列，默认只展示第一列）
    def HasContainerColumns(self, item):
        # print("HasContainerColumns", self.ItemToObject(item))
        return True
    
    # 父类函数（获取父项目）
    def GetParent(self, item):
        if not item.IsOk():
            return dv.NullDataViewItem
        
        node = self.ItemToObject(item)
        if isinstance(node, tuple):  # 项目的父节点是部门
            return self.ObjectToItem(node[0])
        return dv.NullDataViewItem  # 部门的父节点是根
    
    
    # 展开使用
    def GetFirstChild(self, item):
        """获取第一个子节点"""
        children = []
        count = self.GetChildren(item, children)
        if count > 0:
            return (children[0], 1)  # 返回(子项, cookie)
        return (dv.NullDataViewItem, 0)
    
    def GetNextChild(self, item, cookie):
        """获取下一个子节点"""
        children = []
        count = self.GetChildren(item, children)
        if cookie < count:
            return (children[cookie], cookie+1)
        return (dv.NullDataViewItem, 0)


class TreeFrame(wx.Frame):
    def __init__(self, title):
        super().__init__(None, title=title)
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 创建DataViewCtrl
        self.dvc = dv.DataViewCtrl(panel, style=wx.BORDER_THEME|dv.DV_ROW_LINES|dv.DV_VERT_RULES)
        
        # 创建并关联模型
        self.model = TreeDataModel()
        self.dvc.AssociateModel(self.model)
        self._expand_all()
        
        # 添加多列
        self.dvc.AppendTextColumn("名称", 0, width=150)
        self.dvc.AppendTextColumn("负责人", 1, width=100)
        self.dvc.AppendTextColumn("预算", 2, width=100)
        
        # 布局
        sizer.Add(self.dvc, 1, wx.EXPAND|wx.ALL, 5)
        panel.SetSizer(sizer)
        self.Center()

    def _expand_all(self):
        """按钮事件：展开所有节点"""
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._expand_children(root)
    
    def _expand_children(self, parent):
        """递归展开子节点"""
        child, cookie = self.model.GetFirstChild(parent)
        while child.IsOk():
            self.dvc.Expand(child)
            # if self.model.IsContainer(child):
            #     self._expand_children(child)
            child, cookie = self.model.GetNextChild(parent, cookie)

if __name__ == "__main__":
    app = wx.App()
    frame = TreeFrame(title="DataViewCtrl")
    frame.Show()
    app.MainLoop()