"""候选菜单：真窗口里验证选择、自动消失、占用并释放数字键（仅 Windows）。"""

import ctypes
import sys
import time
import unittest
from ctypes import wintypes

from motedeck import candidate_popup_windows as cp


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class CandidatePopupTests(unittest.TestCase):
    def setUp(self):
        self.popup = cp.CandidatePopup()
        self.addCleanup(self.popup.close)
        self.user32 = ctypes.windll.user32
        self.user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self.user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    def key_is_free(self, vk):
        free = bool(self.user32.RegisterHotKey(None, 9100, 0, vk))
        if free:
            self.user32.UnregisterHotKey(None, 9100)
        return free

    def wait(self, predicate, seconds=3.0):
        end = time.time() + seconds
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return predicate()

    def test_fewer_than_two_candidates_do_not_show(self):
        self.assertFalse(self.popup.show([], lambda i, t: None))
        self.assertFalse(self.popup.show(["只有一个"], lambda i, t: None))

    def test_click_chooses_and_releases_the_digit_keys(self):
        chosen = []
        self.assertTrue(self.popup.show(["静静", "近景", "镜镜"], lambda i, t: chosen.append((i, t)), anchor=(50, 50), timeout_seconds=10))
        self.assertTrue(self.wait(lambda: self.popup._hwnd and self.user32.IsWindowVisible(self.popup._hwnd)))
        self.assertTrue(self.wait(lambda: not self.key_is_free(0x32)), "digit keys must be held while shown")
        rect = wintypes.RECT()
        self.user32.GetWindowRect(self.popup._hwnd, ctypes.byref(rect))
        row = (rect.bottom - rect.top) // 3
        self.user32.SendMessageW(self.popup._hwnd, 0x0201, 0, (row + row // 2) << 16 | 20)
        self.assertTrue(self.wait(lambda: chosen))
        self.assertEqual(chosen, [(1, "近景")])
        self.assertTrue(self.wait(lambda: self.key_is_free(0x32)), "digit keys must be released after choosing")
        self.assertFalse(self.user32.IsWindowVisible(self.popup._hwnd))

    def test_it_disappears_by_itself_without_choosing(self):
        chosen = []
        self.popup.show(["静静", "近景"], lambda i, t: chosen.append(i), anchor=(50, 50), timeout_seconds=0.6)
        self.assertTrue(self.wait(lambda: self.popup._hwnd and self.user32.IsWindowVisible(self.popup._hwnd)))
        self.assertTrue(self.wait(lambda: not self.user32.IsWindowVisible(self.popup._hwnd), 4.0))
        self.assertEqual(chosen, [])
        self.assertTrue(self.key_is_free(0x31))


if __name__ == "__main__":
    unittest.main()
