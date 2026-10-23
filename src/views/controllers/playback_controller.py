"""任务视频播放和播放器选择的界面控制器。"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import wx

from src.core.media_players import discover_media_players
from src.core.services.playback_http import PlaybackHTTPServer
from src.core.sys_setting import SysSetting
from src.core.task_encryption import TaskEncryption
from src.core.task_files import output_for
from src.media.encrypted_file import EncryptedMP4Reader, context_from_password, is_encrypted_mp4
from src.storage.task_repository import TaskDataError
from src.schemas.task import TaskType
from src.views.dialogs.player_select_dialog import PlayerSelectDialog
from src.views.dialogs.task_unlock_dialog import TaskUnlockDialog


class PlaybackController:
    """处理播放入口；窗口状态和 wx 控件由 MainFrame 持有。"""

    def __init__(self, frame):
        self.frame = frame

    def _OpenLocalPath(self, path):
        target = Path(path)
        if not target.exists():
            wx.MessageBox('文件或目录已不存在，请确认保存位置或重新下载。', '提示', parent=self.frame)
            return
        if not wx.LaunchDefaultApplication(str(target)):
            wx.MessageBox('无法打开，请检查系统的默认应用设置。', '提示', parent=self.frame)

    def _OpenWithPlayer(self, path, player=None):
        target = Path(path)
        player = player or SysSetting.GetAll()['player_path']
        if not target.is_file():
            wx.MessageBox('视频文件已不存在，请确认保存位置或重新下载。', '提示', parent=self.frame)
            return
        if not player or not Path(player).exists():
            wx.MessageBox('尚未选择有效的播放器，请使用“选择播放器打开”。', '提示', parent=self.frame)
            return
        try:
            if sys.platform == 'darwin':
                subprocess.Popen(['open', '-a', player, '--', str(target)])
            else:
                subprocess.Popen([player, str(target)])
        except (OSError, subprocess.SubprocessError) as error:
            wx.MessageBox(f'无法启动播放器：{error}', '提示', parent=self.frame)

    def _OpenVideo(self, path, task_id=None):
        target = Path(path)
        if is_encrypted_mp4(target):
            self.frame._PlayEncryptedMP4(target, task_id=task_id)
            return
        player = SysSetting.GetAll()['player_path']
        if player and Path(player).exists():
            self.frame._OpenWithPlayer(path, player)
        else:
            self.frame._OpenLocalPath(path)

    def _TaskPlaybackContext(self, task_id):
        """Get the password-derived file-key wrapping context for playback."""
        password_context = self.frame._encryption_context
        if password_context is None:
            password_store = TaskEncryption()
            password_context = password_store.unlock_local()
            if password_context is None:
                dialog = TaskUnlockDialog(self.frame, title='验证加密任务密码')
                try:
                    if dialog.ShowModal() != wx.ID_OK:
                        return None
                    password = dialog.password.GetValue()
                    password_context = password_store.unlock(password)
                    if password_context is None:
                        raise ValueError('密码不正确。')
                    try:
                        password_store.remember_password(password)
                    except OSError:
                        pass
                finally:
                    dialog.Destroy()
        return password_context

    def _FindEncryptedMP4Task(self, path):
        target = Path(path).resolve()
        for task in self.frame.tasks.repository.list_tasks():
            if task.type != TaskType.MP4 or not task.encrypted:
                continue
            download = output_for(task, 'download')
            if download is not None and (task.save_dir / download.output_path).resolve() == target:
                return task.id
        return None

    def _PlayEncryptedMP4(self, path, player=None, task_id=None):
        path = Path(path)
        if task_id is None:
            try:
                task_id = self.frame._FindEncryptedMP4Task(path)
            except (OSError, sqlite3.Error, TaskDataError) as error:
                wx.MessageBox(str(error), '无法读取加密任务', wx.OK | wx.ICON_ERROR, parent=self.frame)
                return
        if task_id is not None:
            try:
                context = self.frame._TaskPlaybackContext(task_id)
            except (OSError, ValueError, sqlite3.Error, TaskDataError) as error:
                wx.MessageBox(str(error), '无法解密 MP4', wx.OK | wx.ICON_ERROR, parent=self.frame)
                return
            if context is None:
                return
            try:
                EncryptedMP4Reader(path, context['wrapping_key'])
            except (OSError, ValueError) as error:
                wx.MessageBox(str(error), '无法解密 MP4', wx.OK | wx.ICON_ERROR, parent=self.frame)
                return
        else:
            context = self.frame._encryption_context
        if context is None:
            dialog = TaskUnlockDialog(self.frame, title='播放加密 MP4')
            try:
                if dialog.ShowModal() != wx.ID_OK:
                    return
                context = context_from_password(path, dialog.password.GetValue())
                EncryptedMP4Reader(path, context['wrapping_key'])
            except (OSError, ValueError) as error:
                wx.MessageBox(str(error), '无法解密 MP4', wx.OK | wx.ICON_ERROR, parent=self.frame)
                return
            finally:
                dialog.Destroy()
        player = player or SysSetting.GetAll()['player_path']
        if not player or not Path(player).exists():
            player = self.frame._ChoosePlayer()
        if not player:
            return
        try:
            if self.frame._playback_service is None:
                self.frame._playback_service = PlaybackHTTPServer(
                    port=SysSetting.GetAll()['playback_port'],
                    on_port_assigned=self.frame._RememberPlaybackPort,
                    http_service=self.frame._local_http_service)
            task_name = None
            if task_id is not None:
                playback_task = self.frame.tasks.repository.get(task_id)
                task_name = playback_task.name if playback_task is not None else None
            token, url = self.frame._playback_service.create_session(
                path, context['wrapping_key'], task_id=task_id, task_name=task_name)
            self.frame._playback_sessions.add(token)
            if sys.platform == 'darwin':
                subprocess.Popen(['open', '-a', player, '--', url])
            else:
                subprocess.Popen([player, url])
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            if 'token' in locals() and self.frame._playback_service is not None:
                self.frame._playback_service.close_session(token)
                self.frame._playback_sessions.discard(token)
            wx.MessageBox(f'无法播放加密 MP4：{error}', '播放失败', parent=self.frame)

    def _PlayEncryptedM3U8(self, task_id, player=None):
        try:
            task = self.frame.tasks.repository.get(task_id)
            if task is None or task.type != TaskType.M3U8:
                raise ValueError('M3U8 任务不存在。')
            context = self.frame._TaskPlaybackContext(task_id)
            if context is None:
                return
            if not task.details.playlist_path:
                raise ValueError('M3U8 任务缺少播放列表路径。')
            playlist = Path(task.save_dir) / task.name / task.details.playlist_path
            EncryptedMP4Reader(playlist, context['wrapping_key'])
        except (OSError, ValueError, sqlite3.Error, TaskDataError) as error:
            wx.MessageBox(str(error), '无法播放加密 M3U8', wx.OK | wx.ICON_ERROR, parent=self.frame)
            return
        player = player or SysSetting.GetAll()['player_path']
        if not player or not Path(player).exists():
            player = self.frame._ChoosePlayer()
        if not player:
            return
        try:
            if self.frame._playback_service is None:
                self.frame._playback_service = PlaybackHTTPServer(
                    port=SysSetting.GetAll()['playback_port'],
                    on_port_assigned=self.frame._RememberPlaybackPort,
                    http_service=self.frame._local_http_service)
            token, url = self.frame._playback_service.create_m3u8_session(task, context['wrapping_key'])
            self.frame._playback_sessions.add(token)
            if sys.platform == 'darwin':
                subprocess.Popen(['open', '-a', player, '--', url])
            else:
                subprocess.Popen([player, url])
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            if 'token' in locals() and self.frame._playback_service is not None:
                self.frame._playback_service.close_session(token)
                self.frame._playback_sessions.discard(token)
            wx.MessageBox(f'无法播放加密 M3U8：{error}', '播放失败', parent=self.frame)

    def _OpenTaskManifest(self, task, path, player=None):
        if task.encrypted and is_encrypted_mp4(path):
            self.frame._PlayEncryptedM3U8(task.task_id, player)
        elif player:
            self.frame._OpenWithPlayer(path, player)
        else:
            self.frame._OpenLocalPath(path)

    def _ChoosePlayerAndOpenManifest(self, task, path):
        player = self.frame._ChoosePlayer()
        if player:
            self.frame._OpenTaskManifest(task, path, player)

    def _ClosePlaybackServers(self):
        self.frame._playback_sessions.clear()
        if self.frame._playback_service is not None:
            self.frame._playback_service.close()
            self.frame._playback_service = None

    def _ChoosePlayer(self):
        current = SysSetting.GetAll()['player_path']
        players = discover_media_players()
        if current and Path(current).exists() and all(Path(candidate).resolve() != Path(current).resolve()
                                                      for _, candidate in players):
            players.insert(0, (f'已选播放器（{Path(current).name}）', current))
        labels = [f'{name} — {Path(candidate).name}' for name, candidate in players]
        dialog = PlayerSelectDialog(self.frame, list(zip(labels, [candidate for _, candidate in players])), current)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            selection = dialog.GetSelection()
            browse_requested = dialog.browse_requested
        finally:
            dialog.Destroy()
        if browse_requested:
            if sys.platform == 'darwin':
                browser = wx.DirDialog(self.frame, '选择播放器应用（例如 VLC.app 或 IINA.app）',
                                       defaultPath=str(Path(current).parent) if current else '/Applications')
            else:
                browser = wx.FileDialog(
                    self.frame, '选择播放器程序', defaultDir=str(Path(current).parent) if current else '',
                    wildcard='可执行文件 (*.exe)|*.exe|所有文件 (*.*)|*.*', style=wx.FD_OPEN)
            try:
                if browser.ShowModal() != wx.ID_OK:
                    return
                player = browser.GetPath()
            finally:
                browser.Destroy()
        else:
            player = players[selection][1]
        try:
            values = SysSetting.GetAll()
            values['player_path'] = player
            SysSetting.Save(values)
        except (OSError, ValueError) as error:
            wx.MessageBox(f'播放器设置保存失败：{error}', '提示', parent=self.frame)
            return None
        return player

    def _ChoosePlayerAndOpen(self, path, task_id=None):
        player = self.frame._ChoosePlayer()
        if player:
            if is_encrypted_mp4(path):
                self.frame._PlayEncryptedMP4(path, player, task_id=task_id)
            else:
                self.frame._OpenWithPlayer(path, player)

    def OnSelectPlayer(self, event):
        player = self.frame._ChoosePlayer()
        if player:
            self.frame._SetPrimaryStatusText(f'已选择播放器：{Path(player).name}')

