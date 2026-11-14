"""CapsWriterController：设备页「语音模型服务」卡片的 QML 适配层。

所有进程探测/启停都在后台线程里做，结果通过信号回到 Qt 线程，避免卡界面。
"""

from __future__ import annotations

import array
import math
import sys
import threading
import time
from pathlib import Path

from . import capswriter_client
from . import capswriter_models
from . import capswriter_service as svc
from . import voice_live
from .qt_settings_qml_tokens import Property, QObject, QTimer, Signal, Slot

_POLL_MS = 4000


def _app_dir() -> Path:
    return svc.app_dir()


class CapsWriterController(QObject):
    changed = Signal()
    busyChanged = Signal()
    messageChanged = Signal(str)
    liveChanged = Signal()
    downloadChanged = Signal()
    _downloadProgress = Signal(str, int, int, float)
    _downloadFinished = Signal(str)
    _statusReady = Signal(object)
    _actionDone = Signal(str)

    def __init__(self, config_root, parent=None) -> None:
        super().__init__(parent)
        self._config_root = Path(config_root)
        self._settings = svc.migrate_existing(self._config_root, _app_dir())
        self._status = svc.status(self._settings, _app_dir())
        self._busy = False
        self._message = ""
        self._statusReady.connect(self._apply_status)
        self._actionDone.connect(self._finish_action)
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._live = {}
        self._live_level = 0.0
        self._test_mode = False
        self._download_cancel = threading.Event()
        self._download_phase = ""  # "" 空闲 / downloading / verifying / extracting
        self._download_done = 0
        self._download_total = 0
        self._download_speed = 0.0
        self._downloadProgress.connect(self._on_download_progress)
        self._downloadFinished.connect(self._on_download_finished)
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(100)
        self._live_timer.timeout.connect(self._tick_live)
        if self._settings.auto_start and self._status.found and not self._status.running:
            QTimer.singleShot(0, self.start)

    # -- properties --
    def _s(self):
        return self._status

    found = Property(bool, lambda self: self._status.found, notify=changed)
    running = Property(bool, lambda self: self._status.running, notify=changed)
    busy = Property(bool, lambda self: self._busy, notify=busyChanged)
    installRoot = Property(str, lambda self: self._status.root, notify=changed)
    port = Property(int, lambda self: self._status.port, notify=changed)
    modelType = Property(str, lambda self: self._status.model_type, notify=changed)
    modelLabel = Property(str, lambda self: self._status.model_label, notify=changed)
    modelInstalled = Property(bool, lambda self: self._status.model_installed, notify=changed)
    autoStart = Property(bool, lambda self: self._settings.auto_start, notify=changed)
    startClient = Property(bool, lambda self: self._settings.start_client, notify=changed)
    message = Property(str, lambda self: self._message, notify=messageChanged)

    def _get_models(self):
        installed = set(self._status.installed_models)
        return [
            {"key": key, "label": label, "installed": key in installed}
            for key, (label, _path) in svc.MODELS.items()
        ]

    models = Property("QVariantList", _get_models, notify=changed)

    def _get_state_text(self) -> str:
        if not self._status.found:
            return "未找到"
        if self._busy:
            return "处理中"
        return "运行中" if self._status.running else "未运行"

    stateText = Property(str, _get_state_text, notify=changed)

    downloading = Property(bool, lambda self: bool(self._download_phase), notify=downloadChanged)
    downloadProgress = Property(
        float,
        lambda self: (self._download_done / self._download_total) if self._download_total else 0.0,
        notify=downloadChanged,
    )

    def _get_download_text(self) -> str:
        if not self._download_phase:
            return ""
        label = {"downloading": "下载中", "verifying": "校验中", "extracting": "解压中"}[self._download_phase]
        done, total = self._download_done / 1048576, self._download_total / 1048576
        text = f"{label} {done:,.0f} / {total:,.0f} MB"
        if self._download_phase == "downloading" and self._download_speed > 0:
            text += f" · {self._download_speed / 1048576:.1f} MB/s"
        return text

    downloadText = Property(str, _get_download_text, notify=downloadChanged)

    def _get_installed_variant(self) -> str:
        if not self._status.root:
            return ""
        package = capswriter_models.installed_package(Path(self._status.root))
        return package.key if package else ""

    installedVariant = Property(str, _get_installed_variant, notify=changed)

    @Property("QVariantList", constant=True)
    def modelVariants(self):
        return [
            {"key": key, "label": pkg.label, "size": pkg.size}
            for key, pkg in capswriter_models.PACKAGES.items()
        ]

    liveLevel = Property(float, lambda self: self._live_level, notify=liveChanged)
    livePhase = Property(str, lambda self: str(self._live.get("phase", "idle")), notify=liveChanged)
    liveText = Property(str, lambda self: str(self._live.get("text", "")), notify=liveChanged)
    liveError = Property(str, lambda self: str(self._live.get("error", "")), notify=liveChanged)
    testMode = Property(bool, lambda self: self._test_mode, notify=liveChanged)

    # -- internals --
    def _tick_live(self) -> None:
        self._live = voice_live.read(self._config_root)
        target = float(self._live.get("level", 0.0) or 0.0)
        if str(self._live.get("phase", "idle")) != "listening":
            target = 0.0
        if target >= self._live_level:
            self._live_level += (target - self._live_level) * 0.65
        else:
            self._live_level = max(target, self._live_level * 0.72)
        if self._live_level < 0.004:
            self._live_level = 0.0
        if self._test_mode:  # 心跳：桥进程只认 3 秒内刷新过的标记
            voice_live.set_test_mode(self._config_root, True)
        self.liveChanged.emit()

    def _set_busy(self, value: bool) -> None:
        if self._busy != value:
            self._busy = value
            self.busyChanged.emit()
            self.changed.emit()

    def _set_message(self, text: str) -> None:
        self._message = text
        self.messageChanged.emit(text)

    def _apply_status(self, status) -> None:
        self._status = status
        self.changed.emit()

    def _finish_action(self, message: str) -> None:
        self._set_message(message)
        self._set_busy(False)
        self.refresh()

    def _run(self, work, done_message: str) -> None:
        if self._busy:
            return
        self._set_busy(True)

        def target() -> None:
            try:
                work()
                self._actionDone.emit(done_message)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                self._actionDone.emit(f"失败：{exc}")

        threading.Thread(target=target, daemon=True, name="capswriter-action").start()

    # -- slots --
    def _on_download_progress(self, phase: str, done: int, total: int, speed: float) -> None:
        self._download_phase, self._download_done = phase, done
        self._download_total, self._download_speed = total, speed
        self.downloadChanged.emit()

    def _on_download_finished(self, message: str) -> None:
        self._download_phase = ""
        self._download_done = self._download_total = 0
        self.downloadChanged.emit()
        self._set_message(message)
        self._set_busy(False)
        self.refresh()

    @Slot(str)
    def downloadModel(self, key: str) -> None:
        """下载并安装 Qwen3-ASR（key：qwen_asr_q4 / qwen_asr_q5）。"""

        package = capswriter_models.PACKAGES.get(key)
        root = self._status.root
        if package is None or not root or self._busy:
            return
        was_running = self._status.running
        self._download_cancel.clear()
        self._set_busy(True)
        self._on_download_progress("downloading", 0, package.size, 0.0)

        def target() -> None:
            try:
                if was_running:  # 正在运行的服务占着模型文件，替换前必须先停
                    svc.stop_services(Path(root))
                capswriter_models.install(
                    Path(root),
                    package,
                    lambda phase, done, total, speed: self._downloadProgress.emit(phase, done, total, speed),
                    self._download_cancel,
                )
                svc.write_model_type(Path(root), "qwen_asr")
                message = "模型已安装"
                if was_running:
                    svc.start_services(Path(root), self._settings.start_client)
                    message += "，服务已重新启动"
            except capswriter_models.ModelDownloadCancelled:
                message = "已取消下载（进度已保存，再次点击可继续）"
                if was_running:
                    svc.start_services(Path(root), self._settings.start_client)
            except Exception as exc:  # noqa: BLE001 - 消息给用户看
                message = f"下载失败：{exc}"
                if was_running:
                    try:
                        svc.start_services(Path(root), self._settings.start_client)
                    except Exception:  # noqa: BLE001
                        pass
            self._downloadFinished.emit(message)

        threading.Thread(target=target, daemon=True, name="capswriter-model-download").start()

    @Slot()
    def cancelDownload(self) -> None:
        self._download_cancel.set()

    @Slot()
    def startLive(self) -> None:
        if not self._live_timer.isActive():
            self._live_timer.start()

    @Slot()
    def stopLive(self) -> None:
        self._live_timer.stop()
        self._live_level = 0.0
        if self._test_mode:
            self._test_mode = False
            voice_live.set_test_mode(self._config_root, False)
        self.liveChanged.emit()

    @Slot(bool)
    def setTestMode(self, value: bool) -> None:
        self._test_mode = bool(value)
        voice_live.set_test_mode(self._config_root, self._test_mode)
        self.liveChanged.emit()

    @Slot()
    def refresh(self) -> None:
        settings = self._settings

        def target() -> None:
            self._statusReady.emit(svc.status(settings, _app_dir()))

        threading.Thread(target=target, daemon=True, name="capswriter-status").start()

    @Slot()
    def start(self) -> None:
        root = self._status.root
        if root:
            self._run(lambda: svc.start_services(Path(root), self._settings.start_client), "已启动")

    @Slot()
    def stop(self) -> None:
        root = self._status.root
        if root:
            self._run(lambda: svc.stop_services(Path(root)), "已停止")

    @Slot()
    def restart(self) -> None:
        root = self._status.root
        if not root:
            return

        def work() -> None:
            svc.stop_services(Path(root))
            svc.start_services(Path(root), self._settings.start_client)

        self._run(work, "已重启")

    @Slot(str)
    def setModel(self, model_type: str) -> None:
        root = self._status.root
        if not root or model_type == self._status.model_type:
            return
        was_running = self._status.running

        def work() -> None:
            svc.write_model_type(Path(root), model_type)
            if was_running:  # 模型在服务启动时加载，换模型必须重启
                svc.stop_services(Path(root))
                svc.start_services(Path(root), self._settings.start_client)

        self._run(work, "已切换模型" + ("并重启服务" if was_running else "，下次启动生效"))

    @Slot(bool)
    def setAutoStart(self, value: bool) -> None:
        self._settings.auto_start = bool(value)
        svc.save_settings(self._config_root, self._settings)
        self.changed.emit()

    @Slot(bool)
    def setStartClient(self, value: bool) -> None:
        self._settings.start_client = bool(value)
        svc.save_settings(self._config_root, self._settings)
        self.changed.emit()

    @Slot(str)
    def setPort(self, text: str) -> None:
        root = self._status.root
        if not root:
            return
        try:
            port = int(text.strip())
            was_running = self._status.running
        except ValueError:
            self._set_message("端口必须是数字")
            return
        if port == self._status.port:
            return

        def work() -> None:
            svc.write_port(Path(root), port)
            if was_running:  # 端口在服务启动时绑定
                svc.stop_services(Path(root))
                svc.start_services(Path(root), self._settings.start_client)

        self._run(work, "已修改端口" + ("并重启服务" if was_running else "，下次启动生效"))

    @Slot()
    def testService(self) -> None:
        """连通测试：把一小段合成音频送进服务，确认能连上并正常返回。

        合成音不是人声，服务回「没听清」也算通过——这里测的是链路和服务，
        不是识别准确率（识别效果请直接按遥控器语音键说话）。
        """

        target = (self._status.root and ("127.0.0.1", self._status.port)) or None
        if target is None:
            self._set_message("没有找到 CapsWriter，请先指定目录")
            return

        def work() -> None:
            rate = capswriter_client.SAMPLE_RATE
            tone = array.array(
                "h",
                (int(6000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(rate * 1.2))),
            )
            started = time.monotonic()
            try:
                capswriter_client.transcribe(tone.tobytes(), target[0], target[1])
            except capswriter_client.CapsWriterAsrError as exc:
                if "没有听清" not in str(exc):
                    raise
            elapsed = int((time.monotonic() - started) * 1000)
            self._actionDone.emit(f"服务正常：1.2 秒测试音频往返 {elapsed} 毫秒")

        def target_fn() -> None:
            try:
                work()
            except Exception as exc:  # noqa: BLE001
                self._actionDone.emit(f"测试失败：{exc}")

        if self._busy:
            return
        self._set_busy(True)
        threading.Thread(target=target_fn, daemon=True, name="capswriter-test").start()

    @Slot()
    def openLogs(self) -> None:
        if self._status.root:
            import os

            logs = Path(self._status.root) / "logs"
            os.startfile(str(logs if logs.is_dir() else self._status.root))  # noqa: S606

    @Slot(str, result=bool)
    def setInstallRoot(self, path: str) -> bool:
        path = path.strip().strip('"')
        if path.startswith("file:///"):
            path = path[8:]
        if not svc.is_install_root(Path(path)):
            self._set_message("该目录不是有效的 CapsWriter 目录（需要 start_server.exe 与 config_server.py）")
            return False
        self._settings.install_root = path
        svc.save_settings(self._config_root, self._settings)
        self._set_message("已更换 CapsWriter 目录")
        self.refresh()
        return True

    @Slot()
    def openFolder(self) -> None:
        if self._status.root:
            import os

            os.startfile(self._status.root)  # noqa: S606 - user's own folder
