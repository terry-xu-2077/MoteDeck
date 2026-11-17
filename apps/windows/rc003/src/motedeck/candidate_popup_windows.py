"""光标处的候选菜单：短词识别不确定时，让用户选「到底是哪个」。

菜单是一个不抢焦点的置顶小窗口（``WS_EX_NOACTIVATE``），用户的光标和输入始终留在
原来的程序里。选择方式：

* 鼠标点某一项（点击不会激活本窗口）；
* 数字键 1..N 或 Esc：只在菜单显示期间用 ``RegisterHotKey`` 临时占用，消失就释放，
  不装全局键盘钩子；
* 几秒不理会自动消失。

选了第 2 项起的候选，就把刚打进去的那几个字退格删掉，再打新的——前提是前台窗口
没变，否则放弃替换，绝不在别的程序里乱删字。

窗口跑在自己的线程和消息循环里，别的线程只通过 ``show``/``hide`` 投递请求。
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Callable, List, Optional, Sequence, Tuple

_LOGGER = logging.getLogger(__name__)

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_CLOSE = 0x0010
WM_ERASEBKGND = 0x0014
WM_MOUSEACTIVATE = 0x0021
WM_TIMER = 0x0113
WM_HOTKEY = 0x0312
WM_LBUTTONDOWN = 0x0201
WM_APP = 0x8000
WM_SHOW_REQUEST = WM_APP + 11
WM_HIDE_REQUEST = WM_APP + 12

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
MA_NOACTIVATE = 3
SW_HIDE = 0
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
DT_LEFT = 0x0
DT_VCENTER = 0x4
DT_SINGLELINE = 0x20
DT_NOPREFIX = 0x800
TRANSPARENT = 1
MONITOR_DEFAULTTONEAREST = 2
VK_ESCAPE = 0x1B
TIMER_ID = 1
HOTKEY_BASE = 0x4D50  # 任意不易冲突的 id 起点
MAX_ITEMS = 5

_BG = 0x002B2B2B       # COLORREF = 0x00BBGGRR
_BG_CURRENT = 0x00383838
_FG = 0x00F0F0F0
_FG_DIM = 0x00A0A0A0
_ACCENT = 0x00FFA96A   # 浅蓝（BGR）
_BORDER = 0x00505050

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = (
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    )


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = (
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    )


class GUITHREADINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    )


class MONITORINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    )


def foreground_window() -> int:
    if sys.platform != "win32":
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return 0


def caret_anchor() -> Tuple[int, int]:
    """候选菜单该出现的屏幕位置（光标下方）；拿不到文本光标就用鼠标位置。"""

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    try:
        hwnd = user32.GetForegroundWindow()
        thread = user32.GetWindowThreadProcessId(hwnd, None)
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(thread, ctypes.byref(info)) and info.hwndCaret:
            rect = info.rcCaret
            if rect.right > rect.left or rect.bottom > rect.top:
                point = wintypes.POINT(rect.left, rect.bottom)
                if user32.ClientToScreen(info.hwndCaret, ctypes.byref(point)):
                    return int(point.x), int(point.y) + 4
    except Exception:  # noqa: BLE001
        pass
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return int(point.x), int(point.y) + 18


class CandidatePopup:
    """线程安全的候选菜单。``show`` 立即返回，选择结果在菜单线程里回调。"""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._hwnd = 0
        self._pending: Optional[Tuple[List[str], Callable[[int, str], None], Tuple[int, int], float]] = None
        # 以下只在菜单线程里读写
        self._items: List[str] = []
        self._on_choose: Optional[Callable[[int, str], None]] = None
        self._registered: List[int] = []
        self._font = None
        self._row_h = 28
        self._width = 160
        self._wndproc = None

    # ---- 对外
    def show(
        self,
        candidates: Sequence[str],
        on_choose: Callable[[int, str], None],
        anchor: Optional[Tuple[int, int]] = None,
        timeout_seconds: float = 4.0,
    ) -> bool:
        if sys.platform != "win32":
            return False
        items = [str(item) for item in candidates if str(item)][:MAX_ITEMS]
        if len(items) < 2:
            return False
        if not self._ensure_thread():
            return False
        position = anchor if anchor is not None else caret_anchor()
        with self._lock:
            self._pending = (items, on_choose, position, timeout_seconds)
        ctypes.windll.user32.PostMessageW(self._hwnd, WM_SHOW_REQUEST, 0, 0)  # type: ignore[attr-defined]
        return True

    def hide(self) -> None:
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_HIDE_REQUEST, 0, 0)  # type: ignore[attr-defined]

    def close(self) -> None:
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)  # type: ignore[attr-defined]
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    # ---- 线程
    def _ensure_thread(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return bool(self._hwnd)
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="motedeck-candidate-popup", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3.0)
        return bool(self._hwnd)

    def _run(self) -> None:
        try:
            self._message_loop()
        except Exception as exc:  # noqa: BLE001 - 候选菜单坏了不能影响识别和打字
            _LOGGER.warning("candidate popup failed: error_type=%s", type(exc).__name__)
        finally:
            self._hwnd = 0
            self._ready.set()

    def _message_loop(self) -> None:
        user32, gdi32, kernel32 = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.kernel32  # type: ignore[attr-defined]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = (
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        )
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.BeginPaint.argtypes = (wintypes.HWND, ctypes.POINTER(PAINTSTRUCT))
        user32.BeginPaint.restype = wintypes.HDC
        user32.EndPaint.argtypes = (wintypes.HWND, ctypes.POINTER(PAINTSTRUCT))
        user32.FillRect.argtypes = (wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HANDLE)
        user32.FrameRect.argtypes = (wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HANDLE)
        user32.DrawTextW.argtypes = (wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT), wintypes.UINT)
        user32.SetWindowPos.argtypes = (
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.SetTimer.argtypes = (wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID)
        user32.KillTimer.argtypes = (wintypes.HWND, ctypes.c_size_t)
        user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.InvalidateRect.argtypes = (wintypes.HWND, wintypes.LPVOID, wintypes.BOOL)
        user32.DestroyWindow.argtypes = (wintypes.HWND,)
        user32.UnregisterClassW.argtypes = (wintypes.LPCWSTR, wintypes.HINSTANCE)
        user32.GetDC.argtypes = (wintypes.HWND,)
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
        user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MONITORINFO))
        gdi32.CreateSolidBrush.argtypes = (wintypes.COLORREF,)
        gdi32.CreateSolidBrush.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = (wintypes.HANDLE,)
        gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HANDLE)
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.SetBkMode.argtypes = (wintypes.HDC, ctypes.c_int)
        gdi32.SetTextColor.argtypes = (wintypes.HDC, wintypes.COLORREF)
        gdi32.GetDeviceCaps.argtypes = (wintypes.HDC, ctypes.c_int)
        gdi32.CreateFontW.restype = wintypes.HANDLE
        gdi32.GetTextExtentPoint32W.argtypes = (wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.SIZE))
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)

        instance = kernel32.GetModuleHandleW(None)
        class_name = f"MoteDeckCandidatePopup.{id(self)}"

        @WNDPROC
        def wndproc(hwnd, message, wparam, lparam):
            try:
                if message == WM_MOUSEACTIVATE:
                    return MA_NOACTIVATE
                if message == WM_ERASEBKGND:
                    return 1
                if message == WM_PAINT:
                    self._paint(hwnd, user32, gdi32)
                    return 0
                if message == WM_SHOW_REQUEST:
                    self._on_show(hwnd, user32, gdi32)
                    return 0
                if message == WM_HIDE_REQUEST or message == WM_TIMER:
                    self._on_hide(hwnd, user32)
                    return 0
                if message == WM_LBUTTONDOWN:
                    y = (int(lparam) >> 16) & 0xFFFF
                    self._choose(hwnd, user32, min(max(y // max(self._row_h, 1), 0), len(self._items) - 1))
                    return 0
                if message == WM_HOTKEY:
                    wid = int(wparam)
                    if wid == HOTKEY_BASE:  # Esc
                        self._on_hide(hwnd, user32)
                    elif HOTKEY_BASE < wid <= HOTKEY_BASE + len(self._items):
                        self._choose(hwnd, user32, wid - HOTKEY_BASE - 1)
                    return 0
                if message == WM_CLOSE:
                    self._on_hide(hwnd, user32)
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("candidate popup message failed: error_type=%s", type(exc).__name__)
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._wndproc = wndproc
        window_class = WNDCLASSW()
        window_class.lpfnWndProc = wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = class_name
        if not user32.RegisterClassW(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, class_name, "MoteDeck candidates",
            WS_POPUP, 0, 0, 10, 10, None, None, instance, None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = int(hwnd)
        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnregisterClassW(class_name, instance)

    # ---- 以下都在菜单线程里
    def _make_font(self, gdi32, hdc):
        dpi = gdi32.GetDeviceCaps(hdc, 90) or 96  # LOGPIXELSY
        height = -int(round(16 * dpi / 96))
        return gdi32.CreateFontW(height, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 5, 0, "Microsoft YaHei UI")

    def _on_show(self, hwnd, user32, gdi32) -> None:
        with self._lock:
            pending, self._pending = self._pending, None
        if pending is None:
            return
        items, on_choose, (x, y), timeout = pending
        self._release_hotkeys(hwnd, user32)
        self._items, self._on_choose = items, on_choose
        hdc = user32.GetDC(hwnd)
        try:
            if self._font is None:
                self._font = self._make_font(gdi32, hdc)
            old = gdi32.SelectObject(hdc, self._font)
            widest = 0
            size = wintypes.SIZE()
            for index, item in enumerate(items):
                label = f"{index + 1}  {item}" + ("  （当前）" if index == 0 else "")
                gdi32.GetTextExtentPoint32W(hdc, label, len(label), ctypes.byref(size))
                widest = max(widest, int(size.cx))
                self._row_h = max(int(size.cy) + 12, 24)
            gdi32.SelectObject(hdc, old)
        finally:
            user32.ReleaseDC(hwnd, hdc)
        self._width = widest + 28
        height = self._row_h * len(items)
        point = wintypes.POINT(int(x), int(y))
        monitor = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            work = info.rcWork
            x = min(max(int(x), work.left), max(work.left, work.right - self._width))
            if y + height > work.bottom:
                y = max(work.top, int(y) - height - 28)  # 下面放不下就放到光标上方
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST, int(x), int(y), self._width, height, SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        user32.InvalidateRect(hwnd, None, True)
        # 数字键 1..N 和 Esc 只在菜单显示期间被占用
        for index in range(len(items)):
            if user32.RegisterHotKey(hwnd, HOTKEY_BASE + 1 + index, 0, 0x31 + index):
                self._registered.append(HOTKEY_BASE + 1 + index)
        if user32.RegisterHotKey(hwnd, HOTKEY_BASE, 0, VK_ESCAPE):
            self._registered.append(HOTKEY_BASE)
        user32.SetTimer(hwnd, TIMER_ID, int(max(timeout, 0.5) * 1000), None)

    def _release_hotkeys(self, hwnd, user32) -> None:
        for wid in self._registered:
            user32.UnregisterHotKey(hwnd, wid)
        self._registered = []

    def _on_hide(self, hwnd, user32) -> None:
        user32.KillTimer(hwnd, TIMER_ID)
        self._release_hotkeys(hwnd, user32)
        user32.ShowWindow(hwnd, SW_HIDE)
        self._items, self._on_choose = [], None

    def _choose(self, hwnd, user32, index: int) -> None:
        items, callback = list(self._items), self._on_choose
        self._on_hide(hwnd, user32)
        if callback is None or not 0 <= index < len(items):
            return
        try:
            callback(index, items[index])
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("candidate choice failed: error_type=%s", type(exc).__name__)

    def _paint(self, hwnd, user32, gdi32) -> None:
        paint = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(paint))
        try:
            old_font = gdi32.SelectObject(hdc, self._font) if self._font else None
            gdi32.SetBkMode(hdc, TRANSPARENT)
            brushes = [gdi32.CreateSolidBrush(color) for color in (_BG, _BG_CURRENT, _BORDER)]
            try:
                for index, item in enumerate(self._items):
                    top = index * self._row_h
                    row = wintypes.RECT(0, top, self._width, top + self._row_h)
                    user32.FillRect(hdc, ctypes.byref(row), brushes[1] if index == 0 else brushes[0])
                    gdi32.SetTextColor(hdc, _ACCENT)
                    number = wintypes.RECT(10, top, 30, top + self._row_h)
                    user32.DrawTextW(hdc, str(index + 1), -1, ctypes.byref(number), DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX)
                    gdi32.SetTextColor(hdc, _FG if index else _FG_DIM)
                    text = item + ("  （当前）" if index == 0 else "")
                    body = wintypes.RECT(30, top, self._width - 6, top + self._row_h)
                    user32.DrawTextW(hdc, text, -1, ctypes.byref(body), DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX)
                outline = wintypes.RECT(0, 0, self._width, self._row_h * len(self._items))
                user32.FrameRect(hdc, ctypes.byref(outline), brushes[2])
            finally:
                for brush in brushes:
                    gdi32.DeleteObject(brush)
            if old_font:
                gdi32.SelectObject(hdc, old_font)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(paint))
