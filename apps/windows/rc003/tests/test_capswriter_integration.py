"""CapsWriter 直连语音：服务管理、协议客户端、后处理、语音键迁移和界面入口。"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from motedeck import (
    capswriter_client,
    capswriter_postprocess,
    capswriter_service,
    config,
    voice_live,
)

_ROOT = Path(__file__).resolve().parents[1]

_CONFIG_SERVER = """\
class ServerConfig:
    addr = '0.0.0.0'
    port = '6016'
    model_type = 'qwen_asr'   # 注释
"""
_CONFIG_CLIENT = """\
class ClientConfig:
    port = '6016'
    trash_punc = '，。,.'
    trash_punc_thresh = 8
    hot = True
    hot_thresh = 0.85
    hot_similar = 0.6
    hot_rule = True
    udp_control_port = 6018
"""


def _install(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "start_server.exe").write_bytes(b"")
    (root / "config_server.py").write_text(_CONFIG_SERVER, encoding="utf-8")
    (root / "config_client.py").write_text(_CONFIG_CLIENT, encoding="utf-8")
    return root


class CapsWriterServiceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _install(Path(self._tmp.name) / "cw")

    def test_reads_model_and_port(self):
        self.assertEqual(capswriter_service.read_model_type(self.root), "qwen_asr")
        self.assertEqual(capswriter_service.read_port(self.root), 6016)

    def test_model_switch_only_touches_that_line(self):
        capswriter_service.write_model_type(self.root, "sensevoice")
        text = (self.root / "config_server.py").read_text(encoding="utf-8")
        self.assertIn("model_type = 'sensevoice'   # 注释", text)
        self.assertIn("addr = '0.0.0.0'", text)
        with self.assertRaises(ValueError):
            capswriter_service.write_model_type(self.root, "nope")

    def test_port_change_updates_server_and_client_but_not_udp(self):
        capswriter_service.write_port(self.root, 6020)
        self.assertEqual(capswriter_service.read_port(self.root), 6020)
        client = (self.root / "config_client.py").read_text(encoding="utf-8")
        self.assertIn("port = '6020'", client)
        self.assertIn("udp_control_port = 6018", client)
        with self.assertRaises(ValueError):
            capswriter_service.write_port(self.root, 70000)

    def test_settings_round_trip_and_migration_writes_nothing(self):
        config_root = Path(self._tmp.name) / "cfg"
        config_root.mkdir()
        settings = capswriter_service.migrate_existing(config_root)
        self.assertEqual(list(config_root.iterdir()), [])
        settings.install_root = str(self.root)
        settings.auto_start = True
        capswriter_service.save_settings(config_root, settings)
        loaded = capswriter_service.load_settings(config_root)
        self.assertEqual(loaded.install_root, str(self.root))
        self.assertTrue(loaded.auto_start)
        self.assertEqual(
            capswriter_service.find_install_root(loaded), self.root
        )


class CapsWriterClientTests(unittest.TestCase):
    def test_pcm_is_encoded_as_float32_base64(self):
        pcm = (1000).to_bytes(2, "little", signed=True) + (-16384).to_bytes(2, "little", signed=True)
        raw = base64.b64decode(capswriter_client.pcm_to_float32_base64(pcm))
        import struct

        first, second = struct.unpack("<2f", raw)
        self.assertAlmostEqual(first, 1000 / 32768.0, places=6)
        self.assertAlmostEqual(second, -0.5, places=6)

    def test_message_matches_protocol(self):
        message = json.loads(capswriter_client._message("t1", 1.5, "", True))
        self.assertEqual(message["task_id"], "t1")
        self.assertEqual(message["source"], "mic")
        self.assertTrue(message["is_final"])
        for field in ("data", "time_start", "seg_duration", "seg_overlap", "context", "language"):
            self.assertIn(field, message)

    def test_empty_audio_and_unreachable_server_raise(self):
        with self.assertRaises(capswriter_client.CapsWriterAsrError):
            capswriter_client.transcribe(b"", "127.0.0.1", 6016)
        with self.assertRaises(capswriter_client.CapsWriterAsrError):
            capswriter_client.transcribe(b"\x00\x00" * 160, "127.0.0.1", 1)


class CapsWriterPostprocessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = _install(Path(self._tmp.name) / "cw")
        (self.root / "hot.txt").write_text("Claude | 克劳德\n", encoding="utf-8")
        (self.root / "hot-rule.txt").write_text("毫安时 = mAh\n", encoding="utf-8")

    def test_hotword_rule_and_punctuation(self):
        processor = capswriter_postprocess.Postprocessor()
        self.assertEqual(processor.process("我在用克劳德写代码。", self.root), "我在用Claude写代码")
        self.assertEqual(processor.process("五百毫安时", self.root), "五百mAh")

    def test_long_sentence_keeps_its_punctuation(self):
        processor = capswriter_postprocess.Postprocessor()
        text = "今天我们讨论一下这个项目的整体进度安排。"
        self.assertEqual(processor.process(text, self.root), text)

    def test_word_list_reloads_when_the_file_changes(self):
        processor = capswriter_postprocess.Postprocessor()
        self.assertEqual(processor.process("伏特", self.root), "伏特")
        time.sleep(0.05)
        (self.root / "hot-rule.txt").write_text("伏特 = V\n", encoding="utf-8")
        os.utime(self.root / "hot-rule.txt", (time.time() + 5, time.time() + 5))
        self.assertEqual(processor.process("伏特", self.root), "V")

    def test_without_install_text_passes_through(self):
        self.assertEqual(capswriter_postprocess.Postprocessor().process(" 你好 ", None), "你好")


class VoiceLiveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_publish_and_read(self):
        voice_live.publish(self.root, phase="idle", text="你好", error="", level=0.0)
        data = voice_live.read(self.root)
        self.assertEqual(data["text"], "你好")

    def test_stale_listening_state_reads_as_idle(self):
        voice_live.publish(self.root, phase="listening", level=0.8)
        path = self.root / voice_live.LIVE_FILE
        old = time.time() - 10
        os.utime(path, (old, old))
        data = voice_live.read(self.root)
        self.assertEqual(data["phase"], "idle")
        self.assertEqual(data["level"], 0.0)

    def test_test_mode_flag_expires(self):
        voice_live.set_test_mode(self.root, True)
        self.assertTrue(voice_live.test_mode_active(self.root))
        flag = self.root / voice_live.TEST_FLAG
        old = time.time() - 10
        os.utime(flag, (old, old))
        self.assertFalse(voice_live.test_mode_active(self.root))
        voice_live.set_test_mode(self.root, False)
        self.assertFalse(flag.exists())


class CapsWriterVoiceKeyTests(unittest.TestCase):
    def test_existing_capswriter_key_is_converted_in_place(self):
        data = config.default_config()
        data["voice_key_configs"] = [
            {"id": 3, "name": "CapsWriter", "hotkey": "caps_lock", "source": "remote", "plugin": ""},
        ]
        new, slot = config.ensure_capswriter_voice_key(data)
        self.assertEqual(slot, 3)
        entry = config.voice_key_config(new, 3)
        self.assertTrue(config.is_capswriter_plugin_id(entry["plugin"]))
        self.assertEqual(entry["hotkey"], "")
        again, same = config.ensure_capswriter_voice_key(new)
        self.assertEqual(same, 3)
        self.assertEqual(len(config.voice_key_configs(again)), len(config.voice_key_configs(new)))

    def test_new_key_is_appended_when_none_exists(self):
        new, slot = config.ensure_capswriter_voice_key(config.default_config())
        self.assertIsNotNone(slot)
        self.assertEqual(config.capswriter_voice_key_slot(new), slot)


_UI_PROBE = textwrap.dedent(
    """
    import os, sys, tempfile, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
    sys.path.insert(0, sys.argv[1])
    from motedeck import qt_settings_app as m
    c = m._load_qt_classes()
    c["QQuickStyle"].setStyle("FluentWinUI3")
    app = c["QGuiApplication"].instance() or c["QGuiApplication"]([])
    m.single_instance.bridge_instance_running = lambda: False
    model = c["ButtonMappingModel"]()
    ctl = c["SettingsController"](model)
    diag = c["DiagnosticsController"](ctl, m.config.config_root(), auto_refresh=False)
    cw = c["CapsWriterController"](m.config.config_root())
    reg = c["qmlRegisterSingletonInstance"]
    for key, inst in (("SettingsController", ctl), ("ButtonMappingModel", model),
                      ("DiagnosticsController", diag), ("CapsWriterController", cw)):
        reg(c[key], "MoteDeckSettings", 1, 0, key, inst)
    engine = c["QQmlApplicationEngine"]()
    qd = m._qml_directory()
    engine.addImportPath(str(qd))
    warnings = []
    engine.warnings.connect(lambda v: warnings.extend(str(x) for x in v))
    engine.load(c["QUrl"].fromLocalFile(str(qd / "main.qml")))
    w = engine.rootObjects()[0]
    w.setWidth(880); w.setHeight(660); w.show()
    from PySide6.QtCore import QObject
    def pump(n=40):
        for _ in range(n):
            app.processEvents(); time.sleep(0.02)
    def find(n): return w.findChild(QObject, n)
    pump()
    out = {}
    out["tiles"] = {n: find(n) is not None for n in
        ("voiceModelServiceRow", "remoteServiceRow", "buttonReceiverRow", "runtimeLogRow")}
    find("voiceTabButton").click(); pump()
    out["voice_controls"] = {n: find(n) is not None for n in
        ("capsWriterToggleButton", "capsWriterVariantCombo", "capsWriterDownloadButton", "capsWriterModelCombo", "capsWriterPortField",
         "capsWriterAutoStartSwitch", "capsWriterStartClientSwitch", "voiceGainSlider",
         "capsWriterTestModeSwitch", "voiceLevelRipple")}
    out["voice_checked"] = bool(find("voiceTabButton").property("checked"))
    find("deviceTabButton").click(); pump()
    out["device_checked"] = bool(find("deviceTabButton").property("checked"))
    find("voiceTabButton").click(); pump()
    out["voice_again"] = bool(find("voiceTabButton").property("checked"))
    out["warnings"] = warnings
    import json
    print(json.dumps(out), flush=True)
    os._exit(0)
    """
)


class CapsWriterUiTests(unittest.TestCase):
    def test_pages_load_switch_and_expose_the_new_controls(self):
        try:
            import PySide6  # noqa: F401
        except ImportError:
            self.skipTest("PySide6 not installed")
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", MOTEDECK_DISABLE_LIVE_INPUT="1")
        result = subprocess.run(
            [sys.executable, "-c", _UI_PROBE, str(_ROOT / "src")],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(data["warnings"], [])
        self.assertTrue(data["tiles"]["voiceModelServiceRow"])
        self.assertTrue(data["tiles"]["remoteServiceRow"])
        self.assertFalse(data["tiles"]["buttonReceiverRow"])
        self.assertFalse(data["tiles"]["runtimeLogRow"])
        self.assertTrue(all(data["voice_controls"].values()), data["voice_controls"])
        # 切页和关窗依赖每个页面都提供 settleInputUiAfterStop 钩子。
        self.assertTrue(data["voice_checked"] and data["device_checked"] and data["voice_again"])


if __name__ == "__main__":
    unittest.main()
