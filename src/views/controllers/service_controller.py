"""本机 HTTP 服务配置、监听状态和状态窗口的界面控制器。"""

from src.core.services.browser_pairing import load_or_create_pairing, pairing_path
from src.core.sys_setting import SysSetting
from src.views.dialogs.service_status_dialog import ServiceStatusDialog


class ServiceController:
    """整合本机 HTTP 服务状态与端口显示。"""

    def __init__(self, frame):
        self.frame = frame

    def _ConfiguredBrowserPort(self):
        if self.frame.browser_bridge is not None:
            return self.frame.browser_bridge.port
        path = pairing_path()
        if path.is_file():
            return load_or_create_pairing(path)[1]
        return 0

    def _PlaybackServiceStatus(self):
        if self.frame._playback_service is not None:
            return self.frame._playback_service.get_status()
        return {'running': False, 'port': None,
                'configured_port': SysSetting.GetAll()['playback_port'], 'sessions': 0}

    def _RememberPlaybackPort(self, port):
        values = SysSetting.GetAll()
        if values['playback_port'] == port:
            return
        values['playback_port'] = port
        SysSetting.Save(values)

    def OnShowLocalServiceStatus(self, _event):
        service = self.frame._local_http_service.get_status()
        playback = self.frame._PlaybackServiceStatus()
        bridge_status = self.frame.browser_bridge.get_status() if self.frame.browser_bridge is not None else None
        try:
            saved_browser_port = self.frame._ConfiguredBrowserPort()
        except (OSError, ValueError):
            saved_browser_port = 0
        port = service['port'] or playback['configured_port'] or saved_browser_port
        endpoint_port = str(port) if port else '<自动分配端口>'
        sessions = playback['sessions']
        plugin_running = bridge_status is not None and bridge_status['running']
        listener = {'running': service['running'], 'port': service['port'] or port}
        features = [
            {'name': '加密视频播放',
             'state': f'{sessions} 个活动播放会话' if sessions else '当前没有活动播放会话',
             'endpoint': f'http://127.0.0.1:{endpoint_port}/play/{{令牌}}/{{文件名}}',
             'details': ('全部播放会话闲置 30 分钟后回收；若浏览器插件接收也未启用，'
                         '共用的 HTTP 服务随之停止。')},
            {'name': '浏览器插件接收',
             'state': (f"运行中 · {bridge_status['pending_captures']} 个待处理地址"
                       if plugin_running else '未启用'),
             'endpoint': f'http://127.0.0.1:{endpoint_port}/capture',
             'details': '配对配置保存后随程序启动；播放功能和插件接收共用上方监听端口。'},
        ]

        dialog = ServiceStatusDialog(self.frame, listener, features)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()

