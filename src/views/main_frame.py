import wx 
import wx.dataview as dv
from src.models.tree_model import MultiColumnTreeModel

from src.managers.file_manager import FileManager
from src.managers.downloader import Downloader
from src.managers.converter import Converter
from src.managers.sys_setting import SysSetting

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
        self._createStatusBar()

        # self.create_main_panel()
        self._createMainPanel()

        self.Center()
        self.Show()

    def _createMenuBar(self):
        # 创建菜单栏
        self.menuBar = wx.MenuBar()
        # 创建文件菜单
        fileMenu = wx.Menu()
        # newItem    = fileMenu.Append(wx.ID_NEW, "&新建\tCtrl-N")
        # openItem   = fileMenu.Append(wx.ID_OPEN, "&打开\tCtrl-O")
        # fileMenu.AppendSeparator()
        downM3U8    = fileMenu.Append(wx.ID_ANY, "&下载M3U8\tCtrl-D")
        downTS      = fileMenu.Append(wx.ID_ANY, "&下载TS")
        fileMenu.AppendSeparator()
        importM3U8  = fileMenu.Append(wx.ID_ANY, "&导入M3U8\tCtrl-D")
        fileMenu.AppendSeparator()
        expandItem  = fileMenu.Append(wx.ID_ANY, "展开全部")
        collapItem  = fileMenu.Append(wx.ID_ANY, "折叠全部")
        refeshItem  = fileMenu.Append(wx.ID_REFRESH, "刷新")
        fileMenu.AppendSeparator()
        exitItem    = fileMenu.Append(wx.ID_EXIT, "&退出")
        # 绑定事件
        # self.Bind(wx.EVT_MENU, self.OnNew, newItem)
        # self.Bind(wx.EVT_MENU, self.OnOpen, openItem)
        self.Bind(wx.EVT_MENU, self.OnExit, exitItem)
		
        # 创建编辑菜单
        viewMenu = wx.Menu()
        self.showToolItem   = viewMenu.Append(wx.ID_ANY, "显示工具栏", kind=wx.ITEM_CHECK)
        self.showStatusItem = viewMenu.Append(wx.ID_ANY, "显示状态栏", kind=wx.ITEM_CHECK)

        self.Bind(wx.EVT_MENU, self.OnToggleStatusBar, self.showStatusItem)

        # 创建关于菜单
        aboutMenu = wx.Menu()
        helpItem    = aboutMenu.Append(wx.ID_ANY, "帮助")
        aboutItem   = aboutMenu.Append(wx.ID_ANY, "关于")

        # 将文件菜单添加到菜单栏
        self.menuBar.Append(fileMenu, "&文件")
        self.menuBar.Append(viewMenu, "&查看")
        self.menuBar.Append(aboutMenu, "&...")
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
        # openButton = self.toolBar.AddTool(wx.ID_OPEN, "Open", wx.Bitmap("icons/tools/open.png"))

        # m3u8image = wx.Image("icons//tools/mp4.png", wx.BITMAP_TYPE_PNG).Rescale(32, 32)
        # refreshimage = wx.Image("icons//tools/refresh.png", wx.BITMAP_TYPE_PNG).Rescale(32, 32)
        m3u8Button      = self.toolBar.AddTool(wx.ID_ANY, "下载M3U8", wx.Bitmap("icons/files/m3u8.png"))
        # m3u8Button      = self.toolBar.AddTool(wx.ID_ANY, "下载M3U8", wx.Bitmap(m3u8image))
        tsButton        = self.toolBar.AddTool(wx.ID_ANY, "下载TS", wx.Bitmap("icons/files/ts-2.png"))
        expandButton    = self.toolBar.AddTool(wx.ID_ANY, "展开全部", wx.Bitmap("icons/tools/expand.png"))
        collapButton    = self.toolBar.AddTool(wx.ID_ANY, "折叠全部", wx.Bitmap("icons/tools/collap.png"))
        refreshButton   = self.toolBar.AddTool(wx.ID_ANY, "刷新", wx.Bitmap("icons/tools/refresh.png"))
        # refreshButton   = self.toolBar.AddTool(wx.ID_ANY, "刷新", wx.Bitmap(refreshimage))
        helpButton      = self.toolBar.AddTool(wx.ID_ANY, "帮助", wx.Bitmap("icons/tools/help.png"))
        aboutButton     = self.toolBar.AddTool(wx.ID_ANY, "关于", wx.Bitmap("icons/tools/about.png"))

        # self.toolBar.Bind(wx.EVT_TOOL, self.OnNew, newButton)
        # self.toolBar.Bind(wx.EVT_TOOL, self.OnOpen, openButton)
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


    def create_main_panel(self):
        """创建主面板和布局"""
        panel = wx.Panel(self)
        # 创建主sizer
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 添加一些控件
        label = wx.StaticText(panel, label="主内容区域")
        text_ctrl = wx.TextCtrl(panel, style=wx.TE_MULTILINE)
        button = wx.Button(panel, label="点击我")
        
        # 添加到sizer
        main_sizer.Add(label, 0, wx.ALL | wx.EXPAND, 5)
        main_sizer.Add(text_ctrl, 1, wx.ALL | wx.EXPAND, 5)
        main_sizer.Add(button, 0, wx.ALL | wx.ALIGN_CENTER, 5)
        
        # 设置面板的sizer
        panel.SetSizer(main_sizer)

    def _createMainPanel(self):
        """创建主面板和布局"""
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        # self.SetBackgroundColour(wx.WHITE)
        # self.SetExtraStyle(wx.BORDER_SIMPLE)
        # sbSetting = wx.StaticBox(self)
        # sbSetting.SetFont(wx.Font(1, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        # sbSetting.SetBackgroundColour(wx.WHITE)
        # sizer = wx.StaticBoxSizer(sbSetting, wx.VERTICAL)

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
        
        # bxSearch.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self.OnSearch)
        # bxSearch.Bind(wx.EVT_TEXT, self.OnSearchText)
        # btnExpand.Bind(wx.EVT_BUTTON, self.OnExpandAll)
        # btnCollapse.Bind(wx.EVT_BUTTON, self.OnCollapseAll)
        # btnRefresh.Bind(wx.EVT_BUTTON, self.OnRefresh)

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
        # self.OnExpandAll(None)

        # 点击选中
        # self.mcTree.Bind(dv.EVT_DATAVIEW_SELECTION_CHANGED, self.OnSelectionChanged)
        # 双颊下载
        # self.mcTree.Bind(dv.EVT_DATAVIEW_ITEM_ACTIVATED, self.OnActivatedChanged)

        listSizer.Add(self.mcTree, proportion=10, flag=wx.EXPAND|wx.TOP, border=5)
        # listSizer.Add(self.mulist, proportion=10, flag=wx.EXPAND|wx.ALL, border=5)
        # self.list.SetBackgroundColour(wx.RED)

        sizer.Add(uriSizer, flag=wx.ALL, border=0)
        sizer.Add(listSizer, proportion=10, flag=wx.EXPAND|wx.ALL, border=0)
        # 设置面板的sizer
        panel.SetSizer(sizer)

    def OnNew(self, event):
        print("New action")
 
    def OnOpen(self, event):
        print("Open action")
 
    def OnExit(self, event):
        self.Close()


    def OnToggleStatusBar(self, e):
        if self.showStatusItem.IsChecked():
            self.statusBar.Show()
        else:
            self.statusBar.Hide()
