"""识别结果后处理：和 CapsWriter 客户端一致——热词音素纠错 → 去末尾标点 → 正则规则。

热词 ``hot.txt``、规则 ``hot-rule.txt`` 和阈值都读用户的 CapsWriter 目录，
文件改了（按修改时间判断）下次识别自动重新加载，在 CapsWriter 里调好的词表
遥控器语音里同样生效。
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

_DEFAULTS = {
    "hot": True,
    "hot_thresh": 0.85,
    "hot_similar": 0.6,
    "hot_rule": True,
    "trash_punc": "，。,.",
    "trash_punc_thresh": 8,
}


def _read_client_options(root: Path) -> Dict[str, object]:
    options = dict(_DEFAULTS)
    try:
        text = (root / "config_client.py").read_text(encoding="utf-8")
    except OSError:
        return options

    def grab(name: str) -> Optional[str]:
        match = re.search(rf"^\s*{name}\s*=\s*(.+?)\s*(?:#.*)?$", text, re.M)
        return match.group(1).strip() if match else None

    for name in ("hot", "hot_rule"):
        raw = grab(name)
        if raw in ("True", "False"):
            options[name] = raw == "True"
    for name in ("hot_thresh", "hot_similar"):
        raw = grab(name)
        try:
            options[name] = float(raw) if raw is not None else options[name]
        except ValueError:
            pass
    raw = grab("trash_punc_thresh")
    try:
        options["trash_punc_thresh"] = int(raw) if raw is not None else options["trash_punc_thresh"]
    except ValueError:
        pass
    raw = grab("trash_punc")
    if raw and raw[0] in "'\"" and raw[-1] == raw[0] and len(raw) >= 2:
        options["trash_punc"] = raw[1:-1]
    return options


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def semantic_units(text: str) -> int:
    """中日韩字符每个算一个单位，连续的字母数字算一个词。"""

    cjk = len(re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + words


class Postprocessor:
    def __init__(self, lexicon=None) -> None:
        # lexicon：场景词表引擎（热度/领域/学习），可选；None 就只用 CapsWriter 词表。
        self._lexicon = lexicon
        self._lock = threading.Lock()
        self._root: Optional[Path] = None
        self._stamp: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._options: Dict[str, object] = dict(_DEFAULTS)
        self._phoneme = None
        self._rules = None

    def _reload_locked(self, root: Path) -> None:
        stamp = (
            _mtime(root / "hot.txt"),
            _mtime(root / "hot-rule.txt"),
            _mtime(root / "config_client.py"),
        )
        if root == self._root and stamp == self._stamp:
            return
        self._root, self._stamp = root, stamp
        self._options = _read_client_options(root)
        self._phoneme = self._rules = None
        try:
            from .capswriter_hw_hot_phoneme import PhonemeCorrector
            from .capswriter_hw_hot_rule import RuleCorrector

            phoneme = PhonemeCorrector(
                float(self._options["hot_thresh"]), float(self._options["hot_similar"])
            )
            hot_text = _read_text(root / "hot.txt")
            if hot_text:
                phoneme.update_hotwords(hot_text)
            rules = RuleCorrector()
            rule_text = _read_text(root / "hot-rule.txt")
            if rule_text:
                rules.update_rules(rule_text)
            self._phoneme, self._rules = phoneme, rules
        except Exception as exc:  # noqa: BLE001 - 词表出错不能挡住识别
            _LOGGER.warning("capswriter hotword load failed: error_type=%s", type(exc).__name__)

    def process(self, text: str, root: Optional[Path]) -> str:
        text = str(text or "").strip()
        raw = text
        if not text:
            return text
        if root is None:
            return self._finish(raw, self._apply_lexicon(text))
        root = Path(root)
        with self._lock:
            self._reload_locked(root)
            options, phoneme, rules = self._options, self._phoneme, self._rules
        try:
            if options["hot"] and phoneme is not None:
                text = phoneme.correct(text, k=10).text
            text = self._apply_lexicon(text)
            punc = str(options["trash_punc"])
            limit = int(options["trash_punc_thresh"])
            if punc and (limit <= 0 or semantic_units(text) <= limit):
                text = re.sub(f"(?<=.)[{re.escape(punc)}]$", "", text)
            if options["hot_rule"] and rules is not None:
                text = rules.substitute(text)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("capswriter postprocess failed: error_type=%s", type(exc).__name__)
        return self._finish(raw, text.strip())

    @property
    def lexicon(self):
        return self._lexicon

    def _apply_lexicon(self, text: str) -> str:
        if self._lexicon is None:
            return text
        return self._lexicon.apply(text)

    def _finish(self, raw: str, final: str) -> str:
        if self._lexicon is not None:
            self._lexicon.observe(raw, final)
        return final


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
