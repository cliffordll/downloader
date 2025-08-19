import wx 

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
        self.Center()

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
        # toolBar = self.CreateToolBar()
        self.toolBar = wx.ToolBar(self)
        # 设置图标大小
        self.toolBar.SetToolBitmapSize((40, 40))

        # 添加工具栏按钮，并绑定事件
        # newButton = self.toolBar.AddTool(wx.ID_NEW, "New", wx.Bitmap("icons/tools/new.png"))
        # openButton = self.toolBar.AddTool(wx.ID_OPEN, "Open", wx.Bitmap("icons/tools/open.png"))

        # m3u8image = wx.Image("icons//tools/mp4.png", wx.BITMAP_TYPE_PNG).Rescale(32, 32)
        # refreshimage = wx.Image("icons//tools/refresh.png", wx.BITMAP_TYPE_PNG).Rescale(32, 32)
        
        m3u8Button      = self.toolBar.AddTool(wx.ID_ANY, "下载M3U8", wx.Bitmap("icons/files/mp4.png"))
        # m3u8Button      = self.toolBar.AddTool(wx.ID_ANY, "下载M3U8", wx.Bitmap(m3u8image))
        tsButton        = self.toolBar.AddTool(wx.ID_ANY, "下载TS", wx.Bitmap("icons/files/flv.png"))
        expandButton    = self.toolBar.AddTool(wx.ID_ANY, "展开全部", wx.Bitmap("icons/tools/expand.png"))
        collapButton    = self.toolBar.AddTool(wx.ID_ANY, "折叠全部", wx.Bitmap("icons/tools/collap.png"))
        refreshButton   = self.toolBar.AddTool(wx.ID_ANY, "刷新", wx.Bitmap("icons/tools/refresh.png"))
        # refreshButton   = self.toolBar.AddTool(wx.ID_ANY, "刷新", wx.Bitmap(refreshimage))
        helpButton      = self.toolBar.AddTool(wx.ID_ANY, "帮助", wx.Bitmap("icons/tools/help.png"))
        aboutButton     = self.toolBar.AddTool(wx.ID_ANY, "关于", wx.Bitmap("icons/tools/about.png"))

        # self.toolBar.Bind(wx.EVT_TOOL, self.OnNew, newButton)
        # self.toolBar.Bind(wx.EVT_TOOL, self.OnOpen, openButton)
        # 启用工具栏
        self.toolBar.Realize()

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
