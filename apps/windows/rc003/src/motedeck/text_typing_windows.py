"""把一段文字直接敲进当前焦点窗口（SendInput + KEYEVENTF_UNICODE）。

不碰剪贴板、不依赖输入法状态；事件带本进程的标记，不会被自己的按键钩子当成
物理按键。
"""

from __future__ import annotations

import ctypes
import sys
from typing import List

from . import win32_input

_KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1
_BATCH_UNITS = 200  # 每批最多这么多 UTF-16 单元，避免一次塞太多被目标程序丢弃


def _units(text: str) -> List[int]:
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


def type_text(text: str) -> int:
    """返回成功送出的事件数；非 Windows 或空文本返回 0。"""

    if sys.platform != "win32" or not text:
        return 0
    units = _units(text)
    sent = 0
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.SendInput.argtypes = (
        ctypes.c_uint,
        ctypes.POINTER(win32_input.INPUT),
        ctypes.c_int,
    )
    user32.SendInput.restype = ctypes.c_uint
    for start in range(0, len(units), _BATCH_UNITS):
        batch = units[start : start + _BATCH_UNITS]
        array = (win32_input.INPUT * (len(batch) * 2))()
        for index, unit in enumerate(batch):
            for offset, flags in ((0, 0x0004), (1, 0x0004 | _KEYEVENTF_KEYUP)):
                keybd = win32_input.KEYBDINPUT(
                    wVk=0,
                    wScan=unit,
                    dwFlags=flags,
                    time=0,
                    dwExtraInfo=win32_input._INPUT_EVENT_EXTRA_INFO,
                )
                array[index * 2 + offset] = win32_input.INPUT(
                    type=_INPUT_KEYBOARD,
                    union=win32_input._INPUT_UNION(ki=keybd),
                )
        sent += int(user32.SendInput(len(array), array, ctypes.sizeof(win32_input.INPUT)))
    return sent
