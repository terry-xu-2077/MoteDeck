"""语音实时状态：常驻进程（识别在那里跑）写，设置窗口读。

两个进程不共享内存，所以用配置目录下的小文件传：``voice-live.json`` 放当前
电平/阶段/最近一次识别文字；``capswriter-test.flag`` 是设置页的「测试模式」
心跳——开着时识别结果只显示在页面上、不敲进焦点窗口。读写都容错，出错只是
页面不刷新，绝不影响识别。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict

LIVE_FILE = "voice-live.json"
TEST_FLAG = "capswriter-test.flag"
TEST_FLAG_TTL_SECONDS = 3.0
_MIN_LEVEL_INTERVAL = 0.08
STALE_SECONDS = 1.5

_state: Dict[str, Any] = {"level": 0.0, "phase": "idle", "text": "", "error": "", "at": 0.0}
_last_write = 0.0


def publish(config_root: Path, *, level_only: bool = False, **fields: Any) -> None:
    global _last_write
    now = time.monotonic()
    if level_only and now - _last_write < _MIN_LEVEL_INTERVAL:
        _state.update(fields)
        return
    _state.update(fields)
    _last_write = now
    try:
        path = Path(config_root) / LIVE_FILE
        tmp = path.with_name(LIVE_FILE + ".tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def read(config_root: Path) -> Dict[str, Any]:
    try:
        data = json.loads((Path(config_root) / LIVE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # 常驻进程只在录音期间不断刷新；文件超过 1.5 秒没更新就当作已经结束，
    # 避免进程崩溃/事件丢失后页面上的电平一直卡在「正在听」。
    try:
        age = time.time() - (Path(config_root) / LIVE_FILE).stat().st_mtime
    except OSError:
        return {}
    if age > STALE_SECONDS and data.get("phase") == "listening":
        data["phase"] = "idle"
        data["level"] = 0.0
    return data


def set_test_mode(config_root: Path, on: bool) -> None:
    path = Path(config_root) / TEST_FLAG
    try:
        if on:
            path.write_text(str(time.time()), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def test_mode_active(config_root: Path) -> bool:
    try:
        return time.time() - (Path(config_root) / TEST_FLAG).stat().st_mtime < TEST_FLAG_TTL_SECONDS
    except OSError:
        return False
