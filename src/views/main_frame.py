from pathlib import Path

import wx 
import wx.dataview as dv
from src.models.tree_model import MultiColumnTreeModel, EVT_ALL_DOWNLOAD

from src.views.downloads.dialog_mu import DownloadDialogMU
from src.views.downloads.dialog_ts import DownloadDialogTS


from src.managers.file_manager import FileManager
from src.managers.downloader import Downloader
from src.managers.converter import Converter
from src.managers.sys_setting import SysSetting
from src.managers.path_manager import PathManager

ICON_ROOT = Path(__file__).resolve().parents[2] / "icons"
ICON_FILES = {
    "open": "tools/open.png",
    "playlist": "files/m3u8.png",
    "segment": "files/ts.png",
    "expand": "tools/expand.png",
    "collapse": "tools/collapse.png",
    "refresh": "tools/refresh.png",
    "help": "tools/help.png",
    "about": "tools/about.png",
}
ICON_SIZES = (24, 30, 36, 48)


def icon_image(filename, size):
    """Load an exact-size PNG, or scale the shared source for other sizes."""
    path = ICON_ROOT / str(size) / filename
    if not path.is_file():
        path = ICON_ROOT / "source" / filename
    image = wx.Image(str(path), wx.BITMAP_TYPE_PNG)
    if not image.IsOk():
        raise ValueError(f"Cannot load icon: {path}")
    if image.GetSize() != wx.Size(size, size):
        image = image.Scale(size, size, wx.IMAGE_QUALITY_HIGH)
    return image


def toolbar_icon(name):
    filename = ICON_FILES[name]
    return wx.BitmapBundle.FromBitmaps([
        wx.Bitmap(icon_image(filename, size)) for size in ICON_SIZES
    ])


class MainFrame(wx.Frame):
    def __init__(self, parent, title):
        # super(MyFrame, self).__init__(parent, title=title)
        super().__init__(parent, title=title)
        self.SetSize(width=1024, height=700)
        
        # 先加载 PNG/JPG，再转为 ICO， 调整尺寸（建议32x32或16x16）
        image = icon_image("app/logo.png", 32)
        icon = wx.Icon(wx.Bitmap(image))
        # icon = wx.Icon()
        # icon.CopyFromBitmap(wx.Bitmap(image))
        self.SetIcon(icon)

        self._createMenuBar()
        self._createToolBar()
        self._createStatusBar()

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
        self.Bind(wx.EVT_MENU, self.OnHelp, helpItem)
        self.Bind(wx.EVT_MENU, self.OnAbout, aboutItem)

        # 将文件菜单添加到菜单栏
        self.menuBar.Append(fileMenu, "&文件")
        self.menuBar.Append(viewMenu, "&查看")
        self.menuBar.Append(aboutMenu, "&帮助")
        # 设置菜单栏
        self.SetMenuBar(self.menuBar)

    def _createToolBar(self):
        self.toolBar = self.CreateToolBar(style=wx.TB_DEFAULT_STYLE)
        self.toolBar.SetToolBitmapSize(self.toolBar.FromDIP(wx.Size(24, 24)))
        self.toolBar.SetToolPacking(self.toolBar.FromDIP(4))
        self.toolBar.SetToolSeparation(self.toolBar.FromDIP(8))
        self.toolBar.SetMargins(self.toolBar.FromDIP(wx.Size(4, 3)))

        def add_tool(tool_id, label, icon):
            bundle = toolbar_icon(icon)
            return self.toolBar.AddTool(tool_id, label, bundle, shortHelp=label)

        openButton = add_tool(wx.ID_OPEN, "打开", "open")
        muButton = add_tool(wx.ID_ANY, "下载M3U8", "playlist")
        tsButton = add_tool(wx.ID_ANY, "下载TS", "segment")
        self.toolBar.AddSeparator()
        expandButton = add_tool(wx.ID_ANY, "展开全部", "expand")
        collapseButton = add_tool(wx.ID_ANY, "折叠全部", "collapse")
        refreshButton = add_tool(wx.ID_ANY, "刷新", "refresh")
        self.toolBar.AddSeparator()
        helpButton = add_tool(wx.ID_ANY, "帮助", "help")
        aboutButton = add_tool(wx.ID_ANY, "关于", "about")

        # self.toolBar.Bind(wx.EVT_TOOL, self.OnNew, newButton)
        # self.toolBar.Bind(wx.EVT_TOOL, self.OnOpen, openButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddMU, muButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAddTS, tsButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnExpandAll, expandButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnCollapseAll, collapseButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnRefresh, refreshButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnHelp, helpButton)
        self.toolBar.Bind(wx.EVT_TOOL, self.OnAbout, aboutButton)
        # 启用工具栏
        self.toolBar.Realize()
        self.showToolItem.Check(self.toolBar.IsShown())

    def _createStatusBar(self):
        self.statusBar = self.CreateStatusBar()
        self.statusBar.SetFieldsCount(2)
        self.statusBar.SetStatusWidths([-1, -3])
        self.statusBar.SetStatusText('就绪', 0)
        self.statusBar.SetStatusText('双击任务或分片行执行“操作”列中的操作', 1)
        self.showStatusItem.Check(self.statusBar.IsShown())

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
        self.model = MultiColumnTreeModel(self)
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
        self.Bind(EVT_ALL_DOWNLOAD, self.OnAllTSDownload)

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

    def _RefreshWithState(self):
        """保存当前所有展开状态, 并刷新视图"""
        expandeds = set()
        root = dv.NullDataViewItem  # 关键点：使用虚拟根节点
        # 保存所有展开状态
        self._SaveExpandState(root, expandeds)

        # 刷新视图（默认折叠）
        self.model.Cleared()

        # 恢复所有展开状态
        self._RestoreExpandState(root, expandeds)

    def _SaveExpandState(self, parent, expandeds):
        """递归保存展开状态"""
        child, cookie = self.model.GetFirstChild(parent)
        while child.IsOk():            
            if self.model.IsContainer(child):
                if self.mcTree.IsExpanded(child):
                    obj = self.model.ItemToObject(child)
                    expandeds.add(obj)
            else:
                self._SaveExpandState(child, expandeds)
            child, cookie = self.model.GetNextChild(parent, cookie)

    def _RestoreExpandState(self, parent, expandeds):
        """根据上一次展开折叠状态，递归展开或折叠"""
        child, cookie = self.model.GetFirstChild(parent)
        while child.IsOk():            
            if self.model.IsContainer(child):
                obj = self.model.ItemToObject(child)
                if obj in expandeds:
                    self.mcTree.Expand(child) 
                # else:
                #     self.mcTree.Collapse(child)
            else:
                self._RestoreExpandState(child)
            child, cookie = self.model.GetNextChild(parent, cookie)


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
        from src.views.tab_setting import SettingsDialog
        previous = SysSetting.GetWorkPath()
        dlg = SettingsDialog(self)
        try:
            if dlg.ShowModal() == wx.ID_OK and previous != SysSetting.GetWorkPath():
                self.model.fileTree = FileManager.GetFileInfos()
                self.model.Cleared()
                self.OnExpandAll(None)
        finally:
            dlg.Destroy()

    def OnExit(self, event):
        self.Close()

    def OnToggleToolBar(self, event):
        '''隐藏展示工具栏'''
        self.toolBar.Show(self.showToolItem.IsChecked())
        self.SendSizeEvent()

    def OnToggleStatusBar(self, event):
        '''隐藏展示状态栏'''
        self.statusBar.Show(self.showStatusItem.IsChecked())
        self.SendSizeEvent()

    def _ShowInformation(self, title, text):
        dlg = wx.MessageDialog(self, '', title, wx.OK | wx.ICON_INFORMATION)
        dlg.SetExtendedMessage(text)
        dlg.SetOKLabel('关闭')
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def OnHelp(self, event):
        self._ShowInformation('使用帮助', (
            '1. 添加任务\n'
            '通过“文件 → 下载M3U8”输入播放列表网址，或通过“下载TS”按分片命名规则创建任务。\n\n'
            '2. 下载与合并\n'
            '双击列表中的任务行下载全部分片，也可双击未下载的分片行单独下载。'
            '全部下载完成后，任务的操作变为“转MP4”，双击即可合并。\n\n'
            '3. 下载设置\n'
            '通过“文件 → 设置”（Ctrl+,）调整保存目录、并发、请求间隔、重试、超时及自动合并。'
            '合并需要 FFmpeg，路径留空时先查找 scripts 目录，再查找系统 PATH。\n\n'
            '4. 界面显示\n'
            '通过“查看”菜单显示或隐藏工具栏、状态栏。'
            '下载窗口内的“？”可查看对应输入框的说明。'
        ))

    def OnAbout(self, event):
        self._ShowInformation('关于视频下载', (
            '视频下载\n\n'
            '支持 M3U8 播放列表、TS 分片下载及 FFmpeg 合并 MP4。\n\n'
            '项目地址：\nhttps://github.com/cliffordll/downloader'
        ))

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
        # 完全重置数据
        self.model.fileTree = FileManager.GetFileInfos()

        # 方法1：刷新全部，默认折叠
        # # 不需要调用 ValueChanged()
        # # 因为 Cleared() 已经通知视图重新加载数据
        # self.model.Cleared()

        # 方法2：记录上一次的展开折叠状态
        self._RefreshWithState()

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
                    self._CreateMP4File(tsSeed, item)
                    return
                elif value == "下载全部":
                    pass
                else:
                    wx.MessageBox(f"【{value}】操作暂不支持。", "提示")
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
                count = self._DownloadFiles(tsSeed, tasks)

                # dlg = wx.MessageBox(f"是否下载{tsSeed}文件中，所有TS文件。", "提示", style=wx.ICON_QUESTION)
                wx.MessageBox(f"共需提交{count}个下载任务。", "提示", style=wx.OK|wx.ICON_INFORMATION)
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

                count = self._DownloadFiles(tsSeed, [(idxj, item)])
                # wx.MessageBox(f"共需提交{count}个下载任务。", "提示", style=wx.OK|wx.ICON_INFORMATION)
        else:
            pass

    
    def OnAllTSDownload(self, event):
        '''所有TS文件都已经下载完毕，修改操作文本'''
        print("OnAllTSDownload", event.GetData())
        payload = event.GetData()
        if not payload:
            return
        fileName = payload.get("fileName", "")
        # print("OnAllTSDownload", fileName)
        self._CreateM3U8File(tsSeed=fileName)

        if SysSetting.GetAll()['auto_merge']:
            for index, task in enumerate(self.model.fileTree.items):
                if task.parent.fileName == fileName and not task.outputs:
                    item = self.model.ObjectToItem(self.model._BuildKey((index,)))
                    self._CreateMP4File(fileName, item)
                    break

        # 方法2：记录上一次的展开折叠状态
        self._RefreshWithState()

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

        tsList = FileManager.GetSegments(absSeed)
        if not tsList or any(idx < 0 or idx >= len(tsList) for idx, _ in tasks):
            wx.MessageBox("播放列表无效或已发生变化，请刷新后重试。", "提示")
            return 0
        missing_source = False
        count = 0
        for task in tasks:
            idx = task[0]
            item = task[1]
            tsName = tsList[idx].name
            absUri = tsList[idx].absUri

            if not tsName:
                continue
            absFile = PathManager.JoinPath(absDir, tsName)
            # 文件已经存在，返回
            if PathManager.IsExists(absFile):
                continue
            if not absUri:
                missing_source = True
                continue
            PathManager.MakeDirsByFile(absFile)        # 判断最后一层目录是否存在（针对ts uri 有/）

            Downloader.DownloadTSFile(absUri, absFile, self._DownloadCall, item)
            count += 1
        if missing_source:
            wx.MessageBox("部分分片缺少下载地址，请提供完整网址或配套的 seed 文件。", "提示")
        return count
    
    def _CreateM3U8File(self, tsSeed):
        '''创建M3U8文件'''
        absSeed = PathManager.GetAbsPath(tsSeed)
        absDir = PathManager.GetAbsDir(absSeed)    # 下载文件路径

        FileManager.CreateM3U8File(absDir, absSeed)

    def _CreateMP4Call(self, flag: bool, fileName: str, item):
        if flag:
            code, newFile = FileManager.GetFileItem(fileName)
            # 插入数据
            self.model.InsertChildData(item, newFile)
            # 刷新视图
            self._RefreshWithState()
        else:
            wx.MessageBox(f"视频文件合并失败", "提示")

    def _CreateMP4File(self, tsSeed: str, item):
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
        if not FileManager.CreatePlaylist(absSeed=absSeed, playDir=absDir, playlist=playlist):
            wx.MessageBox("播放列表无效，无法生成合并清单。", "提示")
            return

        Converter.ConvertTSFile(playlist, outputFile, self._CreateMP4Call, item)
