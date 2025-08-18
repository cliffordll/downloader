import wx 

class MainFrame(wx.Frame):
    def __init__(self, parent, title):
        # super(MyFrame, self).__init__(parent, title=title)
        super().__init__(parent, title=title)

        self._createMenuBar()
        self._createToolBar()
        self.Center()

    def _createMenuBar(self):
        # 创建菜单栏
        menu_bar = wx.MenuBar()
        # 创建文件菜单
        file_menu = wx.Menu()
        new_item    = file_menu.Append(wx.ID_NEW, "&新建\tCtrl-N")
        open_item   = file_menu.Append(wx.ID_OPEN, "&打开\tCtrl-O")
        file_menu.AppendSeparator()
        down_m3u8   = file_menu.Append(wx.ID_NEW, "&下载M3U8\tCtrl-D")
        down_ts     = file_menu.Append(wx.ID_OPEN, "&下载TS")
        file_menu.AppendSeparator()
        exit_item   = file_menu.Append(wx.ID_EXIT, "&退出")
        # 绑定事件
        self.Bind(wx.EVT_MENU, self.OnNew, new_item)
        self.Bind(wx.EVT_MENU, self.OnOpen, open_item)
        self.Bind(wx.EVT_MENU, self.OnExit, exit_item)
		
        # 创建编辑菜单
        view_menu = wx.Menu()

        # 创建关于菜单
        about_menu = wx.Menu()

        # 将文件菜单添加到菜单栏
        menu_bar.Append(file_menu, "&文件")
        menu_bar.Append(view_menu, "&查看")
        menu_bar.Append(about_menu, "&关于")
        # 设置菜单栏
        self.SetMenuBar(menu_bar)

    def _createToolBar(self):
        # 创建工具栏
        # toolbar = self.CreateToolBar()
        toolbar = wx.ToolBar(self)
        # 设置图标大小
        toolbar.SetToolBitmapSize((20, 20))

        # 添加工具栏按钮，并绑定事件
        new_button = toolbar.AddTool(wx.ID_NEW, "New", wx.Bitmap("icons/file.png"))
        open_button = toolbar.AddTool(wx.ID_OPEN, "Open", wx.Bitmap("icons/open.png"))
        help_button = toolbar.AddTool(wx.ID_OPEN, "Help", wx.Bitmap("icons/help.png"))
        about_button = toolbar.AddTool(wx.ID_OPEN, "About", wx.Bitmap("icons/about.png"))
        toolbar.Bind(wx.EVT_TOOL, self.OnNew, new_button)
        toolbar.Bind(wx.EVT_TOOL, self.OnOpen, open_button)
        # 启用工具栏
        toolbar.Realize()

 
    def OnNew(self, event):
        print("New action")
 
    def OnOpen(self, event):
        print("Open action")
 
    def OnExit(self, event):
        self.Close()