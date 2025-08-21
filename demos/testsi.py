import wx
import wx.dataview as dv

class FourLevelModel(dv.PyDataViewModel):
    def __init__(self, data):
        dv.PyDataViewModel.__init__(self)
        self.data = data
        
    def IsContainer(self, item):
        if not item.IsOk():  # 根节点
            return True
            
        obj = self.ItemToObject(item)
        
        # 检查是否是容器节点
        if isinstance(obj, str):
            current = self.data
            path = self._find_path(obj)
            
            if not path:
                return False
                
            for level in path[:-1]:
                current = current.get(level, {})
                
            return isinstance(current.get(path[-1], None), dict)
            
        return False
    
    def GetChildren(self, parent, children):
        if not parent.IsOk():  # 根节点
            for root_name in self.data.keys():
                children.append(self.ObjectToItem(root_name))
            return len(self.data)
            
        obj = self.ItemToObject(parent)
        path = self._find_path(obj)
        
        if not path:
            return 0
            
        current = self.data
        for level in path:
            current = current.get(level, {})
            
        if isinstance(current, dict):
            for key in current.keys():
                children.append(self.ObjectToItem(key))
            return len(current)
        elif isinstance(current, list):
            for item in current:
                children.append(self.ObjectToItem(item))
            return len(current)
            
        return 0
    
    def GetValue(self, item, col):
        obj = self.ItemToObject(item)
        # return dv.Variant(str(obj))
        return "---"
    
    def _find_path(self, target, current=None, path=None):
        """递归查找目标节点的路径"""
        if path is None:
            path = []
        if current is None:
            current = self.data
            
        if isinstance(current, dict):
            for key, value in current.items():
                new_path = path + [key]
                if key == target:
                    return new_path
                found = self._find_path(target, value, new_path)
                if found:
                    return found
        elif isinstance(current, list):
            for item in current:
                if item == target:
                    return path + [item]
                    
        return None
    
    # def ObjectToItem(self, obj):
    #     return dv.DataViewItem(obj)
    
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
        super().__init__(None, title="四级节点示例", size=(600, 400))
        
        # 创建四级结构数据
        data = {
            "集团公司": {
                "华东分公司": {
                    "技术部": ["前端组", "后端组"],
                    "市场部": ["推广组", "策划组"]
                },
                "华北分公司": {
                    "人事部": ["招聘组", "培训组"]
                }
            }
        }
        
        # 创建控件
        panel = wx.Panel(self)
        self.dvc = dv.DataViewCtrl(panel, style=dv.DV_VERT_RULES|dv.DV_HORIZ_RULES)
        
        # 创建模型
        self.model = FourLevelModel(data)
        self.dvc.AssociateModel(self.model)
        self.model.DecRef()
        
        # 添加列
        self.dvc.AppendTextColumn("组织架构", 0, width=300)
        
        # 布局
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.dvc, 1, wx.EXPAND|wx.ALL, 5)
        panel.SetSizer(sizer)
        
        # 默认展开到第三级
        wx.CallAfter(self.expand_to_level, 3)
    
    def expand_to_level(self, level):
        """展开到指定层级"""
        def _expand(item, current_level):
            if current_level >= level:
                return
                
            model = self.dvc.GetModel()
            child, cookie = model.GetFirstChild(item)
            
            while child.IsOk():
                self.dvc.Expand(child)
                _expand(child, current_level + 1)
                child, cookie = model.GetNextChild(item, cookie)
        
        _expand(dv.DataViewItem(), 0)  # 从根节点开始

app = wx.App()
frame = MyFrame()
frame.Show()
app.MainLoop()