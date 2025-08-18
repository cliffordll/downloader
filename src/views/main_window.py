
import wx
from src.views.tab_index import TabIndex
from src.views.tab_download import TabDownload
from src.views.tab_setting import TabSetting

class MainWindow(wx.Frame):
    def __init__(self, parent, title):
        super().__init__(parent, title = title) 
        self.SetTitle(title)
        # self.SetSize(width=728, height=450)
        # self.SetSize(width=1024, height=768)
        self.SetSize(width=1024, height=700)

        # 先加载 PNG/JPG，再转为 ICO， 调整尺寸（建议32x32或16x16）
        image = wx.Image("icons/logo.png", wx.BITMAP_TYPE_PNG).Rescale(32, 32)
        icon = wx.Icon(wx.Bitmap(image))
        # icon = wx.Icon()
        # icon.CopyFromBitmap(wx.Bitmap(image))
        self.SetIcon(icon)

        panel = wx.Panel(self)
        # 主布局
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Choicebook 控件
        self.books = wx.Listbook(panel)
        img_list= wx.ImageList(20, 20)
        img_list.Add(wx.Bitmap(wx.Image("icons/index.png", wx.BITMAP_TYPE_ANY).Scale(20, 20)))
        img_list.Add(wx.Bitmap(wx.Image("icons/download.png", wx.BITMAP_TYPE_ANY).Scale(20, 20)))
        img_list.Add(wx.Bitmap(wx.Image("icons/setting.png", wx.BITMAP_TYPE_ANY).Scale(20, 20)))
        self.books.AssignImageList(img_list)

        # 创建第一个页面
        self.tabIndex = TabIndex(self.books)
        # tabOne.SetBackgroundColour("Gray")
        self.books.AddPage(self.tabIndex, "首页", imageId=0)
        # 创建第二个页面
        tabDownload = TabDownload(self.books)
        self.books.AddPage(tabDownload, "下载", imageId=1)
        # self.books.Add 
        # 创建第三个页面
        tabSetting = TabSetting(self.books)
        self.books.AddPage(tabSetting, "设置", imageId=2)
        self.books.ChangeSelection(0)
 
        sizer.Add(self.books, 1, wx.ALL|wx.EXPAND, 5)

        panel.SetSizer(sizer)
        # self.Layout()

        tabDownload.Bind(wx.EVT_BUTTON, self.OnDownloadClicked)
        self.Bind(wx.EVT_LISTBOOK_PAGE_CHANGED, self.OnPageChanged)
        self.Bind(wx.EVT_LISTBOOK_PAGE_CHANGING, self.OnPageChanging)

        # 解决在Mac上大小错误
        # wx.CallLater(100, self.AdjustSize)
        self.Center()
        
    # def AdjustSize(self):
    #     self.GetTreeCtrl().InvalidateBestSize()
    #     self.SendSizeEvent()

    def OnPageChanged(self, e):
        old = e.GetOldSelection()
        new = e.GetSelection()
        sel = self.books.GetSelection()
        # print("OnPageChanged,  old:%d, new:%d, sel:%d\n" % (old, new, sel))
        e.Skip()
 
    def OnPageChanging(self, e):
        old = e.GetOldSelection()
        new = e.GetSelection()
        sel = self.books.GetSelection()
        # print("OnPageChanging,  old:%d, new:%d, sel:%d\n" % (old, new, sel))
        e.Skip()

    def OnDownloadClicked(self, e):
        print("MainWindow.OnDownloadClicked")
        self.tabIndex.OnRefresh(None)