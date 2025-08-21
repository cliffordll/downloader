import wx 
import wx.dataview as dv
from src.models.tree_model import MultiColumnTreeModel

from src.views.downloads.dialog_mu import DownloadDialogMU
from src.views.downloads.dialog_ts import DownloadDialogTS


from src.managers.file_manager import FileManager
from src.managers.downloader import Downloader
from src.managers.converter import Converter
from src.managers.sys_setting import SysSetting
from src.managers.path_manager import PathManager

class MainFrame(wx.Frame):
    def __init__(self, parent, title):
        # super(MyFrame, self).__init__(parent, title=title)
        super().__init__(parent, title=title)
        self.SetSize(width=1024, height=700)
        
        # 先加载 PNG/JPG，再转为 ICO， 调整尺寸（建议32x32或16x16）
        image = wx.Image("icons/logo.png", wx.BITMAP_TYPE_PNG).Rescale(32, 32)
        icon = wx.Icon(wx.Bitmap(image))
        # icon = wx.Icon()
        # icon.CopyFromBitmap(wx.Bitmap(image))
        self.SetIcon(icon)

        self._createMenuBar()
        self._createToolBar()
        # self._createStatusBar()

        self._createMainPanel()

        self.Center()
        self.Show()

    def _createMenuBar(self):
        # 创建菜单栏
        self.menuBar = wx.MenuBar()
        # 创建文件菜单
        fileMenu = wx.Menu()
        openItem    = fileMenu.Append(wx.ID_OPEN, "&打开\tCtrl-O")
        fileMenu.AppendSeparator()
        addMUItem   = fileMenu.Append(wx.ID_ANY, "&下载M3U8\tCtrl-M")
        addTSItem   = fileMenu.Append(wx.ID_ANY, "&下载TS\tCtrl-T")
        fileMenu.AppendSeparator()
        # importM3U8  = fileMenu.Append(wx.ID_ANY, "&导入M3U8\tCtrl-D")
        # fileMenu.AppendSeparator()
        expandItem  = fileMenu.Append(wx.ID_ANY, "展开全部")
        collapseItem = fileMenu.Append(wx.ID_ANY, "折叠全部")
        refeshItem  = fileMenu.Append(wx.ID_REFRESH, "刷新")
        fileMenu.AppendSeparator()
        settingItem  = fileMenu.Append(wx.ID_ANY, "设置\tCtrl-,")
        exitItem    = fileMenu.Append(wx.ID_EXIT, "&退出")
        # 绑定事件
        self.Bind(wx.EVT_MENU, self.OnOpen, openItem)
        self.Bind(wx.EVT_MENU, self.OnAddMU, addMUItem)
        self.Bind(wx.EVT_MENU, self.OnAddTS, addTSItem)
        self.Bind(wx.EVT_MENU, self.OnExpandAll, expandItem)
        self.Bind(wx.EVT_MENU, self.OnCollapseAll, collapseItem)
        self.Bind(wx.EVT_MENU, self.OnRefresh, refeshItem)
        self.Bind(wx.EVT_MENU, self.OnSetting, settingItem)
        self.Bind(wx.EVT_MENU, self.OnExit, exitItem)
		
        # 创建编辑菜单
        viewMenu = wx.Menu()
        self.showToolItem   = viewMenu.Append(wx.ID_ANY, "显示工具栏", kind=wx.ITEM_CHECK)
        self.showStatusItem = viewMenu.Append(wx.ID_ANY, "显示状态栏", kind=wx.ITEM_CHECK)
        self.Bind(wx.EVT_MENU, self.OnToggleToolBar, self.showToolItem)
        self.Bind(wx.EVT_MENU, self.OnToggleStatusBar, self.showStatusItem)

        # 创建关于菜单
        aboutMenu = wx.Menu()
        helpItem    = aboutMenu.Append(wx.ID_ANY, "帮助")
        aboutItem   = aboutMenu.Append(wx.ID_ANY, "关于")

        # 将文件菜单添加到菜单栏
        self.menuBar.Append(fileMenu, "&文件")
        self.menuBar.Append(viewMenu, "&查看")
        self.menuBar.Append(aboutMenu, "&帮助")
        # 设置菜单栏
        self.SetMenuBar(self.menuBar)

    def _createToolBar(self):
        # 创建工具栏
        self.toolBar = self.CreateToolBar()
        # 设置图标大小
        # self.toolBar.SetToolBitmapSize((40, 40))
        self.toolBar.SetToolBitmapSize((20, 20))

        # 添加工具栏按钮，并绑定事件
        # newButton = self.toolBar.AddTool(wx.ID_NEW, "New", wx.Bitmap("icons/tools/new.png"))
        openButton      = self.toolBar.AddTool(wx.ID_OPEN, "打开", wx.Bitmap("icons/tools/open.png"))

        # m3u8image = wx.Image("icons//tools/mp4.png", wx.BITMAP_TYPE_PNG).Rescale(32, 32)
        # refreshimage = wx.Image("icons//tools/refresh.png", wx.BITMAP_TYPE_PNG).Rescale(32, 32)
        muButton        = self.toolBar.AddTool(wx.ID_ANY, "下载M3U8", wx.Bitmap("icons/files/m3u8.png"))
        # m3u8Button      = self.toolBar.AddTool(wx.ID_ANY, "下载M3U8", wx.Bitmap(m3u8image))
        tsButton        = self.toolBar.AddTool(wx.ID_ANY, "下载TS", wx.Bitmap("icons/files/ts-2.png"))
        expandButton    = self.toolBar.AddTool(wx.ID_ANY, "展开全部", wx.Bitmap("icons/tools/expand.png"))
        collapseButton  = self.toolBar.AddTool(wx.ID_ANY, "折叠全部", wx.Bitmap("icons/tools/collap.png"))
        refreshButton   = self.toolBar.AddTool(wx.ID_ANY, "刷新", wx.Bitmap("icons/tools/refresh.png"))
        # refreshButton   = self.toolBar.AddTool(wx.ID_ANY, "刷新", wx.Bitmap(refreshimage))
        helpButton      = self.toolBar.AddTool(wx.ID_ANY, "帮助", wx.Bitmap("icons/tools/help.png"))
        aboutButton     = self.toolBar.AddTool(wx.ID_ANY, "关于", wx.Bitmap("icons/tools/about.png"))

        # self.toolBar.Bind(wx.EVT_TOOL, self.OnNew, newButton)
        # self.toolBar.Bind(wx.EVT_TOOL, self.OnOpen, openButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddMU, muButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddTS, tsButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnExpandAll, expandButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnCollapseAll, collapseButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnRefresh, refreshButton)
        # 启用工具栏
        self.toolBar.Realize()# 添加分隔线
        self.toolBar.AddSeparator()

    def _createStatusBar(self):
      # 创建状态栏
        self.statusBar = self.CreateStatusBar()
        # 设置状态栏字段数量（多个字段可用分隔符分隔）
        self.statusBar.SetFieldsCount(3)
        # 字段占比
        self.statusBar.SetStatusWidths([-2, -1, -1])
        self.statusBar.SetStatusStyles([wx.SB_RAISED, wx.SB_RAISED, wx.SB_RAISED])
        # 设置字段的 显示内同
        self.statusBar.SetStatusText(u'状态信息0', 0)
        self.statusBar.SetStatusText(u'', 1)
        self.statusBar.SetStatusText(u'状态信息2', 2)

    def _createMainPanel(self):
        """创建主面板和布局"""
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # 首航布局
        uriSizer = wx.BoxSizer(wx.HORIZONTAL)
        bxSearch = wx.SearchCtrl(panel)
        btnExpand = wx.Button(panel, label="全部展开")
        btnCollapse = wx.Button(panel, label="全部折叠")
        btnRefresh = wx.Button(panel, label="刷新")
        uriSizer.Add(bxSearch, proportion=50, flag=wx.EXPAND|wx.TOP|wx.BOTTOM|wx.RIGHT, border=5)
        uriSizer.Add(btnExpand, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        uriSizer.Add(btnCollapse, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        uriSizer.Add(btnRefresh, proportion=1, flag=wx.EXPAND|wx.ALL, border=5)
        
        bxSearch.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.OnSearch)
        bxSearch.Bind(wx.EVT_TEXT, self.OnSearchText)
        btnExpand.Bind(wx.EVT_BUTTON, self.OnExpandAll)
        btnCollapse.Bind(wx.EVT_BUTTON, self.OnCollapseAll)
        btnRefresh.Bind(wx.EVT_BUTTON, self.OnRefresh)

        # 多列树布局
        # self.tsList = wx.TextCtrl(self, style=wx.TE_MULTILINE|wx.TE_LEFT|wx.TE_READONLY|wx.TE_RICH2)
        listSizer = wx.BoxSizer(wx.HORIZONTAL)
        # 创建并关联模型
        self.model = MultiColumnTreeModel()
        # 创建DataViewCtrl
        self.mcTree = dv.DataViewCtrl(panel, -1, style=wx.BORDER_THEME|dv.DV_ROW_LINES|dv.DV_VERT_RULES|dv.DV_VARIABLE_LINE_HEIGHT|dv.DV_ROW_LINES)
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
        listSizer.Add(self.mcTree, proportion=10, flag=wx.EXPAND|wx.TOP, border=5)
        # listSizer.Add(self.mulist, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)
        # self.list.SetBackgroundColour(wx.RED)

        # 点击选中
        # self.mcTree.Bind(dv.EVT_DATAVIEW_SELECTION_CHANGED, self.OnSelectionChanged)
        # 双击下载
        self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, self.OnActivatedChanged)

        sizer.Add(uriSizer, flag=wx.ALL, border=0)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND|wx.ALL, border=0)
        # 设置面板的sizer
        panel.SetSizer(sizer)

    ###################################
    ### 事件所需函数
    ###################################
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
    
    def _SearchItems(self, text):
        """搜索匹配项"""
        if not text:
            return
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        found_item = self._FindItem(root, text.lower())
        if found_item.IsOk():
            self.mcTree.Select(found_item)
            self.mcTree.EnsureVisible(found_item)

    def _RecursiveExpand(self, item, expand):
        """递归展开/折叠"""
        child, cookie = self.model.GetFirstChild(item)
        while child.IsOk():            
            if self.model.IsContainer(child):
                self.mcTree.Expand(child) if expand else self.mcTree.Collapse(child)
            else:
                self._RecursiveExpand(child, expand)
            child, cookie = self.model.GetNextChild(item, cookie)
    
    ###################################
    ### 操作菜单的事件
    ################################### 
    def OnOpen(self, event):
        print("Open action")
    
    def OnAddMU(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogMU(None, "M3U8 URI", workPath)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.OnRefresh(None)
        dlg.Destroy()

    def OnAddTS(self, event):
        workPath = SysSetting.GetWorkPath()
        dlg = DownloadDialogTS(None, "M3U8 TS", workPath)
        result = dlg.ShowModal()
        if result == wx.OK:
            # 创建新任务成功，自动刷新页面
            self.OnRefresh(None)
        dlg.Destroy()
    
    def OnSetting(self, event):
        print("OnSetting")

    def OnExit(self, event):
        self.Close()

    def OnToggleToolBar(self, event):
        '''隐藏展示工具栏'''
        if self.showToolItem.IsChecked():
            self.toolBar.Show()
        else:
            self.toolBar.Hide()

    def OnToggleStatusBar(self, event):
        '''隐藏展示状态栏'''
        if self.showStatusItem.IsChecked():
            self.statusBar.Show()
        else:
            self.statusBar.Hide()

    ###################################
    ### 操作树得事件
    ###################################
    def OnSearch(self, event):
        """搜索按钮事件"""
        # print("on_search", event.GetString())
        self._SearchItems(event.GetString())
    
    def OnSearchText(self, event):
        """搜索文本变化事件"""
        # print("on_search_text", event.GetString())
        self._SearchItems(event.GetString())

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
                tasks = []
                childs = []
                self.model.GetChildren(item, childs)
                for idxj, child in enumerate(childs):
                    tasks.append((idxj, child))
                self._DownloadFiles(tsSeed, tasks)

                # dlg = wx.MessageBox(f"是否下载{tsSeed}文件中，所有TS文件。", "提示", style=wx.ICON_QUESTION)
                wx.MessageBox(f"共需提交{len(tasks)}个下载任务。", "提示", style=wx.OK|wx.ICON_INFORMATION)
        elif len(objs) == 2:    # 子节点
            parent = self.model.GetParent(item)
            if parent.IsOk():
                idxj = objs[1]
                # tsName = self.model.GetValue(item, 1)
                tsSeed = self.model.GetValue(parent, 1)
                # # dlg = wx.MessageBox(f"是否下载{tsName}文件。", "提示", style=wx.ICON_QUESTION)
                # dlg = wx.MessageBox(f"是否下载{tsName}文件。", "提示", style=wx.OK|wx.ICON_QUESTION)
                # if dlg != wx.ID_OK:
                #     return

                self._DownloadFiles(tsSeed, [(idxj, item)])
        else:
            pass

    
    def _DownloadCall(self, flag: bool, fileName: str, item):
        if flag:
            flag, fileItem = FileManager.GetFileItem(fileName)

            # # 方法一，更新所有数据，并展开
            # self.OnRefresh(None)
            # self.OnExpandAll(None)
            # 方法二，更新 item 的低0列数据 （不用刷新整个页面，还能保证上一次是否展开）
            self.model.SetValue(variant=fileItem, item=item, col=0)
            self.model.ValueChanged(item, 0)
        else:
            # wx.MessageBox(f"错误信息：{fileName}", "提示")
            pass
        return

    def _DownloadFiles(self, tsSeed: str, tasks: list):
        '''下载文件并修改视图状态'''
        absSeed = PathManager.GetAbsPath(tsSeed)
        absDir = PathManager.GetAbsDir(absSeed)    # 下载文件路径

        tsList = FileManager.GetSegmentList(absSeed=absSeed)
        for task in tasks:
            idx = task[0]
            item = task[1]
            tsName = tsList[idx].name
            absUri = tsList[idx].absUri

            if not tsName or not absUri:
                continue
            absFile = PathManager.JoinPath(absDir, tsName)
            # 文件已经存在，返回
            if PathManager.IsExists(absFile):
                continue
            PathManager.MakeDirsByFile(absFile)        # 判断最后一层目录是否存在（针对ts uri 有/）

            Downloader.DownloadTSFile(absUri, absFile, self._DownloadCall, item)
        return
    
    def _CreatePlaylist(self, tsSeed: str):
        absSeed = PathManager.GetAbsPath(tsSeed)
        absDir = PathManager.GetAbsDir(absSeed)

        # # 2. 确保下载文件一定存在
        playlist = PathManager.JoinPath(absDir, "playlist.txt")
        outputFile = PathManager.JoinPath(absDir, "output.mp4")

        # 文件已经存在，返回
        if PathManager.IsExists(outputFile):
            wx.MessageBox(f"视频文件已经存在。", "提示")
            return

        print("absSeed", absSeed)
        print("absDir", absDir)
        print("playlist", playlist)
        # 写palylist文件
        absFile = FileManager.CreatePlaylist(absSeed=absSeed, playDir=absDir, playlist=playlist)

        Converter.ConvertTSFile(playlist, outputFile)