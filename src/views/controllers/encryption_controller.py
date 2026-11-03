"""加密任务可见性、密码和任务文件转换的界面控制器。"""

import sqlite3
from threading import Thread

import wx

from src.core.task_encryption import (
    TaskEncryption, plan_task_artifacts, prepare_task_artifacts, publish_task_artifacts,
)
from src.media.encrypted_file import is_encrypted_mp4
from src.schemas.task import TaskType
from src.storage.task_repository import TaskDataError, TaskConflictError
from src.views.components.icons import toolbar_icon
from src.views.dialogs.task_password_dialog import TaskPasswordDialog
from src.views.dialogs.task_unlock_dialog import TaskUnlockDialog


class TaskArtifactCleanupWarning(OSError):
    """任务状态已更新，但转换后的旧文件未能全部清理。"""


class EncryptionController:
    """管理密码状态和任务加解密，并通过 MainFrame 更新视图。"""

    def __init__(self, frame):
        self.frame = frame

    def _SetTaskEncrypted(self, task_id, encrypted, *, progress_dialog=None, raise_errors=False):
        def report(message, title, style):
            if raise_errors:
                raise ValueError(message)
            wx.MessageBox(message, title, style, parent=self.frame)

        try:
            busy = self.frame.runner.busy(task_id)
        except (ValueError, OSError, sqlite3.Error, TaskDataError, TaskConflictError) as error:
            if raise_errors:
                raise
            wx.MessageBox(str(error), '任务加密失败' if encrypted else '任务解密失败',
                          wx.OK | wx.ICON_ERROR, parent=self.frame)
            return False
        if busy:
            report('任务正在下载、检测或合并，请等待结束后再加密或解密。',
                   '暂时无法操作', wx.OK | wx.ICON_INFORMATION)
            return False
        task = self.frame.tasks.repository.get(task_id)
        if task is None:
            report('任务不存在，请刷新后重试。', '任务加密失败' if encrypted else '任务解密失败',
                   wx.OK | wx.ICON_ERROR)
            return False
        if task.type not in (TaskType.MP4, TaskType.M3U8):
            report('当前任务类型不支持加密或解密文件。', '暂时无法操作', wx.OK | wx.ICON_INFORMATION)
            return False
        media_paths = plan_task_artifacts(task, encrypted)
        for media_path, destination, _complete in media_paths:
            if destination != media_path and (destination.exists() or destination.is_symlink()):
                report(f'目标文件已存在，未修改原文件：{destination.name}',
                       '目标文件已存在', wx.OK | wx.ICON_WARNING)
                return False
        needs_key = encrypted or task.encrypted or any(
            is_encrypted_mp4(path) for path, _destination, _complete in media_paths)
        context = self.frame._encryption_context
        if needs_key and context is None:
            context = self.frame._GetEncryptionContextForOperation()
            if context is None:
                return
        if encrypted:
            try:
                if not TaskEncryption().is_configured():
                    report('请先从“任务”菜单设置加密任务密码。',
                           '尚未设置密码', wx.OK | wx.ICON_INFORMATION)
                    return False
            except ValueError as error:
                if raise_errors:
                    raise
                wx.MessageBox(str(error), '读取密码失败', wx.OK | wx.ICON_ERROR, parent=self.frame)
                return False
        try:
            prepared = []
            metadata_updated = False
            if media_paths:
                failures = []

                def convert_media():
                    try:
                        prepared.extend(prepare_task_artifacts(media_paths, encrypted, context))
                    except Exception as error:
                        failures.append(error)

                worker = Thread(target=convert_media, name='task-encryption')
                action = '加密' if encrypted else '解密'
                owns_progress = progress_dialog is None
                progress = progress_dialog or wx.ProgressDialog(
                    f'任务{action}', '正在转换本地任务文件…', parent=self.frame,
                    style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME)
                try:
                    worker.start()
                    while worker.is_alive():
                        if task is not None:
                            progress.Pulse(f'正在转换：{task.name}')
                        worker.join(0.05)
                finally:
                    if owns_progress:
                        progress.Destroy()
                if failures:
                    raise failures[0]
            self.frame.runner.set_task_encrypted(task_id, encrypted, artifacts_prepared=True)
            metadata_updated = True
            cleanup_errors = publish_task_artifacts(prepared)
            if self.frame._playback_service is not None:
                self.frame._playback_sessions.difference_update(
                    self.frame._playback_service.close_task_sessions(task_id))
            self.frame.OnRefresh(None)
            if cleanup_errors:
                action = '加密' if encrypted else '解密'
                names = '、'.join(source.name for source, _destination, _working, _complete in prepared
                                  if source.exists())
                message = (f'{action}状态已更新，但旧文件未能全部清理：{names}\n'
                           '请检查文件占用后手动删除旧文件。')
                if raise_errors:
                    raise TaskArtifactCleanupWarning(message)
                wx.MessageBox(message, f'{action}完成但清理未完成',
                              wx.OK | wx.ICON_WARNING, parent=self.frame)
            return True
        except Exception as error:
            if not metadata_updated:
                for source, destination, working, _complete in prepared:
                    artifacts = {path for path in (destination, working) if path != source}
                    for artifact in artifacts:
                        try:
                            if artifact.exists():
                                artifact.unlink()
                        except OSError:
                            pass
            if raise_errors:
                raise
            wx.MessageBox(str(error), '任务加密失败' if encrypted else '任务解密失败',
                          wx.OK | wx.ICON_ERROR, parent=self.frame)

    def _GetEncryptionContextForOperation(self):
        """Load the locally protected key, asking only for first-time setup/migration."""
        try:
            password_store = TaskEncryption()
            if not password_store.is_configured():
                dialog = TaskPasswordDialog(self.frame, has_existing=False)
                try:
                    if dialog.ShowModal() != wx.ID_OK:
                        return None
                    password = dialog.GetPassword()
                    local_credential_saved = password_store.set_password(password)
                    if not local_credential_saved:
                        wx.MessageBox('密码已创建，但本机凭据未能保存；后续加密或解密操作仍需输入密码。',
                                      '本机凭据不可用', wx.OK | wx.ICON_WARNING, parent=self.frame)
                    return password_store.unlock(password)
                finally:
                    dialog.Destroy()
        except (OSError, ValueError) as error:
            wx.MessageBox(str(error), '读取密码失败', wx.OK | wx.ICON_ERROR, parent=self.frame)
            return None

        try:
            context = password_store.unlock_local()
        except (OSError, ValueError) as error:
            wx.MessageBox(str(error), '读取本地密码失败', wx.OK | wx.ICON_ERROR, parent=self.frame)
            return None
        if context is not None:
            return context

        dialog = TaskUnlockDialog(self.frame, title='验证加密任务密码')
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return None
            password = dialog.password.GetValue()
            context = password_store.unlock(password)
            if context is None:
                wx.MessageBox('密码不正确。', '验证失败', wx.OK | wx.ICON_ERROR, parent=self.frame)
            else:
                # 旧配置只有不可逆校验值；验证成功后保存一份由当前 Windows
                # 用户 DPAPI 保护的凭据，以便后续加解密操作直接复用。
                try:
                    password_store.remember_password(password)
                except OSError as error:
                    wx.MessageBox(f'密码验证成功，但无法保存本地凭据；后续操作仍需验证密码。\n{error}',
                                  '本地密码保存失败', wx.OK | wx.ICON_WARNING, parent=self.frame)
            return context
        finally:
            dialog.Destroy()

    def OnUnlockEncryptedTasks(self, _event):
        try:
            password_store = TaskEncryption()
            if not password_store.is_configured():
                wx.MessageBox('请先从“任务”菜单设置加密任务密码。',
                              '尚未设置密码', wx.OK | wx.ICON_INFORMATION, parent=self.frame)
                return
        except ValueError as error:
            wx.MessageBox(str(error), '读取密码失败', wx.OK | wx.ICON_ERROR, parent=self.frame)
            return

        dialog = TaskUnlockDialog(self.frame)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            context = password_store.unlock(dialog.password.GetValue())
            if context is None:
                wx.MessageBox('密码不正确。', '无法解锁', wx.OK | wx.ICON_ERROR, parent=self.frame)
                return
            try:
                password_store.remember_password(dialog.password.GetValue())
            except OSError as error:
                wx.MessageBox(f'已解锁任务，但无法保存本地凭据；下次操作仍需验证密码。\n{error}',
                              '本地密码保存失败', wx.OK | wx.ICON_WARNING, parent=self.frame)
            self.frame._encryption_context = context
            self.frame._encrypted_tasks_unlocked = True
            self.frame._SyncLockEncryptedTasksEnabled()
            self.frame._ApplyTaskFilter(force=True)
        finally:
            dialog.Destroy()

    def OnLockEncryptedTasks(self, _event):
        """Clear the in-memory unlock state and immediately hide encrypted tasks."""
        if not self.frame._encrypted_tasks_unlocked:
            return
        self.frame._encrypted_tasks_unlocked = False
        for task in self.frame.model.fileTree.items:
            if task.encrypted:
                self.frame.model.selected_task_ids.discard(
                    task.task_id or task.parent.fileName)
        self.frame._encryption_context = None
        self.frame._ClosePlaybackServers()
        self.frame._SyncLockEncryptedTasksEnabled()
        self.frame._ApplyTaskFilter(force=True)
        self.frame._SetPrimaryStatusText('加密任务已隐藏。')

    def OnEncryptedTasksTool(self, event):
        if self.frame._encrypted_tasks_unlocked:
            self.frame.OnLockEncryptedTasks(event)
        else:
            self.frame.OnUnlockEncryptedTasks(event)

    def _SyncLockEncryptedTasksEnabled(self):
        unlocked = self.frame._encrypted_tasks_unlocked
        self.frame.lockEncryptedTasksItem.Enable(unlocked)
        if self.frame.toolBar is not None:
            tool_id = self.frame._encryptedTasksTool.GetId()
            label = '隐藏加密任务' if unlocked else '显示加密任务'
            icon = 'lock' if unlocked else 'unlock'
            self.frame.toolBar.SetToolNormalBitmap(tool_id, toolbar_icon(icon))
            self.frame._encryptedTasksTool.SetLabel(label)
            self.frame.toolBar.SetToolShortHelp(tool_id, label)
            self.frame.toolBar.EnableTool(tool_id, True)

    def OnSetEncryptedPassword(self, _event):
        password_store = TaskEncryption()
        try:
            has_existing = password_store.is_configured()
        except ValueError as error:
            wx.MessageBox(str(error), '读取密码失败', wx.OK | wx.ICON_ERROR, parent=self.frame)
            return

        dialog = TaskPasswordDialog(self.frame, has_existing=has_existing)
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            encrypted_files = []
            if has_existing and dialog.GetCurrentPassword() != dialog.GetPassword():
                for task in self.frame.tasks.repository.list_tasks():
                    if not task.encrypted or task.type not in (TaskType.MP4, TaskType.M3U8):
                        continue
                    candidates = [source for source, _destination, _complete
                                  in plan_task_artifacts(task, not task.encrypted)]
                    for path in candidates:
                        if not path.exists():
                            continue
                        if not is_encrypted_mp4(path):
                            raise ValueError(f'任务标记为已加密，但文件并非有效加密容器：{path.name}')
                        encrypted_files.append(path)
            local_credential_saved = password_store.set_password(
                dialog.GetPassword(), current_password=dialog.GetCurrentPassword(),
                encrypted_files=encrypted_files)
            self.frame._encryption_context = password_store.unlock(dialog.GetPassword())
            self.frame._encrypted_tasks_unlocked = True
            self.frame._ClosePlaybackServers()  # 密码变更后立即撤销旧播放 URL 和内存会话。
            self.frame._SyncLockEncryptedTasksEnabled()
            message = '加密任务密码已保存。'
            if not local_credential_saved:
                message += '\n本机凭据未能保存，后续加密或解密操作仍需输入密码。'
            wx.MessageBox(message, '设置完成',
                          wx.OK | wx.ICON_INFORMATION, parent=self.frame)
        except (OSError, ValueError) as error:
            wx.MessageBox(str(error), '设置密码失败', wx.OK | wx.ICON_ERROR, parent=self.frame)
        finally:
            dialog.Destroy()

