import wx
import wx.dataview as dv
from src.models.tree_model import MultiColumnTreeModel

from src.managers.file_manager import FileManager
from src.managers.downloader import Downloader
from src.managers.converter import Converter
from src.managers.sys_setting import SysSetting

class TabIndex(wx.Panel):
    """
    This will be the first notebook tab
    """
    def __init__(self, parent):
        super().__init__(parent=parent, id=wx.ID_ANY)
        sizer = wx.BoxSizer(wx.VERTICAL)
        # self.SetBackgroundColour(wx.WHITE)
        # self.SetExtraStyle(wx.BORDER_SIMPLE)
        # sbSetting = wx.StaticBox(self)
        # sbSetting.SetFont(wx.Font(1, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        # sbSetting.SetBackgroundColour(wx.WHITE)
        # sizer = wx.StaticBoxSizer(sbSetting, wx.VERTICAL)

        # 首航布局
        uriSizer = wx.BoxSizer(wx.HORIZONTAL)

        bxSearch = wx.SearchCtrl(self)
        btnExpand = wx.Button(self, label="全部展开")
        btnCollapse = wx.Button(self, label="全部折叠")
        btnRefresh = wx.Button(self, label="刷新")
        uriSizer.Add(bxSearch, proportion=50, flag=wx.EXPAND|wx.BOTTOM|wx.RIGHT, border=5)
        uriSizer.Add(btnExpand, proportion=1, flag=wx.EXPAND|wx.BOTTOM|wx.RIGHT|wx.LEFT, border=5)
        uriSizer.Add(btnCollapse, proportion=1, flag=wx.EXPAND|wx.BOTTOM|wx.RIGHT|wx.LEFT, border=5)
        uriSizer.Add(btnRefresh, proportion=1, flag=wx.EXPAND|wx.BOTTOM|wx.LEFT, border=5)
        
        bxSearch.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.OnSearch)
        bxSearch.Bind(wx.EVT_TEXT, self.OnSearchText)
        btnExpand.Bind(wx.EVT_BUTTON, self.OnExpandAll)
        btnCollapse.Bind(wx.EVT_BUTTON, self.OnCollapseAll)
        btnRefresh.Bind(wx.EVT_BUTTON, self.OnRefresh)

        # self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_READONLY|wx.TE_RICH2)
        listSizer = wx.BoxSizer(wx.HORIZONTAL)
        # 创建并关联模型
        self.model = MultiColumnTreeModel()
        # 创建DataViewCtrl
        self.mcTree = dv.DataViewCtrl(self, -1, style=wx.BORDER_THEME|dv.DV_ROW_LINES|dv.DV_VERT_RULES|dv.DV_VARIABLE_LINE_HEIGHT|dv.DV_ROW_LINES)
        self.mcTree.AssociateModel(self.model)
        # 添加多列
        self.mcTree.AppendTextColumn("序列", 0, width=80)
        # # 自定义列
        # renderer = dv.DataViewTextRenderer()
        # renderer.EnableEllipsize(wx.ELLIPSIZE_END)
        # self.mcTree.AppendColumn(dv.DataViewColumn("文件名", renderer, 1, width=180, align=wx.ALIGN_LEFT))
        # self.mcTree.AppendTextColumn("文件名", 1, width=500)
        self.mcTree.AppendTextColumn("文件名", 1, width=500)

        self.mcTree.AppendTextColumn("文件大小", 2, width=100, align=wx.ALIGN_RIGHT)
        self.mcTree.AppendTextColumn("修改时间", 3, width=140)
        self.mcTree.AppendTextColumn("操作", 4, width=60)
        # self.mcTree.AppendTextColumn("下载地址", 5)
        # self.model.DecRef()  # 避免内存泄漏
        self.OnExpandAll(None)

        # 点击选中
        # self.mcTree.Bind(dv.EVT_DATAVIEW_SELECTION_CHANGED, self.OnSelectionChanged)
        # 双击下载
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, self.OnActivatedChanged)

        listSizer.Add(self.mcTree, proportion=10, flag=wx.EXPAND|wx.TOP, border=5)
        # listSizer.Add(self.mulist, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)
        # self.list.SetBackgroundColour(wx.RED)

        sizer.Add(uriSizer, flag=wx.ALL, border=0)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND|wx.ALL, border=0)
        self.SetSizer(sizer)
    
    def _RecursiveExpand(self, item, expand):
        """递归展开/折叠"""
        child, cookie = self.model.GetFirstChild(item)
        while child.IsOk():            
            if self.model.IsContainer(child):
                self.mcTree.Expand(child) if expand else self.mcTree.Collapse(child)
            else:
                self._RecursiveExpand(child, expand)
            child, cookie = self.model.GetNextChild(item, cookie)
    
    def _SearchItems(self, text):
        """搜索匹配项"""
        if not text:
            return
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        found_item = self._FindItem(root, text.lower())
        if found_item.IsOk():
            self.mcTree.Select(found_item)
            self.mcTree.EnsureVisible(found_item)
    
    def _FindItem(self, parent, search_text):
        """递归查找匹配项"""
        child, cookie = self.model.GetFirstChild(parent)
        while child.IsOk():
            # 检查当前项
            for col in range(self.model.GetColumnCount()):
                value = self.model.GetValue(child, col).lower()
                if search_text in value:
                    return child
            # 如果是容器，递归检查子项
            if self.model.IsContainer(child):
                found_in_child = self._FindItem(child, search_text)
                if found_in_child.IsOk():
                    return found_in_child
            child, cookie = self.model.GetNextChild(parent, cookie)
        return dv.NullDataViewItem

    def _DownloadCall(self, flag: bool, message: str, item):
        if flag:
            flag, fileItem = FileManager.GetFileItem(message)

            # # 方法一，更新所有数据，并展开
            # self.OnRefresh(None)
            # self.OnExpandAll(None)
            # 方法二，更新 item 的低0列数据 （不用刷新整个页面，还能保证上一次是否展开）
            self.model.SetValue(variant=fileItem, item=item, col=0)
        else:
            wx.MessageBox(f"错误信息：{message}", "提示")
        return

    def _DownloadFile(self, tsSeed: str, index: int, tsName: str, item):
        '''下载文件并修改视图状态'''
        absSeed = SysSetting.GetAbsolutePath(tsSeed)
        absFile = SysSetting.GetAbsolutePath(tsName)
        # 文件已经存在，返回
        if SysSetting.IsExists(absFile):
            return
        
        absUri = FileManager.GetUriByIdx(absSeed, index)
        if not absUri:
            return
        
        SysSetting.MakeDirsByFile(absFile)        # 判断最后一层目录是否存在（针对ts uri 有/）
        Downloader.DownloadTSFile(absUri, absFile, self._DownloadCall, item)
        return
    
    def _CreatePlaylist(self, tsSeed: str):
        absSeed = SysSetting.GetAbsolutePath(tsSeed)

        # 写palylist文件
        playDir = SysSetting.GetAbsoluteDir(absSeed)
        # # 2. 确保下载文件一定存在
        # SysSetting.MakeDirsByPath(playDir)
        playlist = SysSetting.GetPath(playDir, "playlist.txt")
        outputFile = SysSetting.GetPath(playDir, "output.mp4")

        # 文件已经存在，返回
        if SysSetting.IsExists(outputFile):
            wx.MessageBox(f"视频文件已经存在。", "提示")
            return

        print("absSeed", absSeed)
        print("playDir", playDir)
        print("playlist", playlist)
        absFile = FileManager.CreatePlaylist(absSeed=absSeed, playDir=playDir, playlist=playlist)

        Converter.ConvertTSFile(playlist, outputFile)
     
    
    ###################################
    ### 操作树得事件
    ###################################
    def OnExpandAll(self, event):
        """展开所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, True)
    
    def OnCollapseAll(self, event):
        """折叠所有节点"""
        # root = self.dvc.GetTopItem()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        self._RecursiveExpand(root, False)
        
    def OnSearch(self, event):
        """搜索按钮事件"""
        # print("on_search", event.GetString())
        self._SearchItems(event.GetString())
    
    def OnSearchText(self, event):
        """搜索文本变化事件"""
        # print("on_search_text", event.GetString())
        self._SearchItems(event.GetString())

    def OnRefresh(self, event):
        # print("OnRefresh")
        # self.model.Cleared()  # 清空并重新加载
        # self.model.Resort()    # 重置模型

        # 完全重置数据
        self.model.fileTree = FileManager.GetFileInfos()
        # self.model.Reset()  # 完全重置模型
        # self.model.Refresh()

        # 不需要调用 ValueChanged()
        # 因为 Cleared() 已经通知视图重新加载数据
        self.model.Cleared()


    def OnActivatedChanged(self, event):
        """选中项变化事件"""
        item = event.GetItem()
        if not item.IsOk():
            return
        value = self.model.GetValue(item, 4)
        if not value:
            return

        # 解析索引
        keys = self.model.ItemToObject(item)
        objs = self.model.ParseKey(keys)
        if len(objs) == 1:      # 父节点
            if item.IsOk():
                tsSeed = self.model.GetValue(item, 1)
                if value == "转MP4":
                    # wx.MessageBox(f"将要合并多少个文件。", "提示")
                    self._CreatePlaylist(tsSeed)
                    return

                # # dlg = wx.MessageBox(f"是否下载{tsSeed}文件中，所有TS文件。", "提示", style=wx.ICON_QUESTION)
                # dlg = wx.MessageBox(f"是否下载{tsSeed}文件中，所有TS文件。", "提示", style=wx.OK|wx.ICON_INFORMATION)
                # if dlg != wx.ID_OK:
                #     return
                childs = []
                self.model.GetChildren(item, childs)
                for idxj, child in enumerate(childs):
                    tsName = self.model.GetValue(child, 1)
                    self._DownloadFile(tsSeed, idxj, tsName, child)
        elif len(objs) == 2:    # 子节点
            idxj = objs[1]
            tsName = self.model.GetValue(item, 1)
            parent = self.model.GetParent(item)
            if parent.IsOk():
                tsSeed = self.model.GetValue(parent, 1)
                # # dlg = wx.MessageBox(f"是否下载{tsName}文件。", "提示", style=wx.ICON_QUESTION)
                # dlg = wx.MessageBox(f"是否下载{tsName}文件。", "提示", style=wx.OK|wx.ICON_QUESTION)
                # if dlg != wx.ID_OK:
                #     return

                self._DownloadFile(tsSeed, idxj, tsName, item)
        else:
            pass