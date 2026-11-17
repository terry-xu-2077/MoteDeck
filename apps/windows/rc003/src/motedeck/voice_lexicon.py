"""语音场景词表：用户自建的「场景」、热度冷却和自动学习。

一个「场景」是用户自己起名的一份词表（比如写分镜、写代码、做医疗记录），里面有
正确的词，以及每个词常被听错成什么（别名）。说话时用到某个场景的词越多，这个
场景的「热度」越高；停下来以后热度按半衰期指数冷却。热度够高的场景，它的词和
别名才会参与识别后的纠错，冷下来就自动退出——所以「静静→近景」只在写分镜的时候
生效，平时说「安安静静」不会被改。别名一律属于某个场景，没有全局别名（要全局
替换请用 CapsWriter 自己的 hot-rule.txt）。

学习有两个来源，学到的别名挂在被纠正的那个词所属的场景下，都不碰用户在
CapsWriter 里手写的 hot.txt：

* 重说模式：短时间内连续说了几句很短、读音相近的话，最后一句是某个场景的词，
  前面几句就很可能都是它的误识别，出现次数够了就记成别名；
* 用户选择：在候选菜单里明确选了「这个词其实是那个」。

两个进程各管各的文件，避免互相覆盖：

* ``voice-lexicon.json``  设置窗口写（场景、开关、半衰期、忘记某条学到的别名、清空历史时间）；
* ``voice-lexicon-state.json``  常驻进程写（热度、最近识别、学到的别名）。

所有读写都容错：文件损坏只是退回默认值，绝不影响识别和打字。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

SETTINGS_FILE = "voice-lexicon.json"
STATE_FILE = "voice-lexicon-state.json"

DEFAULT_HALF_LIFE_MINUTES = 30.0
ON_THRESHOLD = 2.0    # 热度达到这个值，自动场景开始生效
OFF_THRESHOLD = 0.8   # 降到这个值以下才退出（两条线拉开，避免来回切换）
HIT_CAP = 3           # 一句话最多给一个场景加这么多分，防止重复一个词刷分
HISTORY_LIMIT = 300
HISTORY_DAYS = 7
LEARNED_HALF_LIFE_DAYS = 7.0
LEARNED_PRUNE_BELOW = 0.25
REPEAT_WINDOW_SECONDS = 20.0   # 两句之间隔多久还算「重说」
REPEAT_MAX_UNITS = 4           # 重说模式只看很短的话
REPEAT_MIN_SIMILARITY = 0.6    # 前面几句和正确词的读音要足够像
PROMOTE_AFTER = 2              # 同一个错法出现这么多次才记成别名
STREAK_LIMIT = 8
CANDIDATE_MIN_SIMILARITY = 0.8
MAX_SCENES = 30
MAX_TERMS_PER_SCENE = 800
MAX_ALIASES_PER_SCENE = 800
MAX_NAME_CHARS = 30

MODE_AUTO = "auto"
MODE_ON = "on"
MODE_OFF = "off"
MODES = (MODE_AUTO, MODE_ON, MODE_OFF)

# 「导入示例」用的模板在 voice_lexicon_examples；导入后就是普通的用户场景，可以随便改删。
from .voice_lexicon_examples import EXAMPLE_SCENES  # noqa: E402


# ───────────────────────── 通用小工具 ─────────────────────────

def semantic_units(text: str) -> int:
    cjk = len(re.findall(r"[㐀-鿿぀-ヿ가-힯]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", text))
    return cjk + words


def decayed(score: float, at: float, now: float, half_life_seconds: float) -> float:
    """按半衰期把 ``at`` 时刻的分数冷却到 ``now``。"""

    if half_life_seconds <= 0:
        return 0.0
    elapsed = max(0.0, now - at)
    return float(score) * (0.5 ** (elapsed / half_life_seconds))


def _atomic_write(path: Path, data: Dict[str, Any]) -> bool:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pinyin(text: str) -> str:
    try:
        from pypinyin import Style, lazy_pinyin

        return "".join(lazy_pinyin(text, style=Style.NORMAL))
    except Exception:  # noqa: BLE001 - 没有 pypinyin 时退回原文比较
        return text.lower()


def pinyin_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    try:
        from rapidfuzz import fuzz

        return float(fuzz.ratio(_pinyin(left), _pinyin(right))) / 100.0
    except Exception:  # noqa: BLE001
        return 0.0


def new_scene_id() -> str:
    return uuid.uuid4().hex[:8]


def split_terms(text: str) -> List[str]:
    """把用户输入的词表文字（逗号、顿号、分号、换行分隔）拆成词。"""

    parts = re.split(r"[,，、;；\n\r]+", str(text or ""))
    return list(dict.fromkeys(part.strip() for part in parts if part.strip()))


def make_scene(name: str, terms: Optional[List[str]] = None,
               aliases: Optional[List[Tuple[str, str]]] = None, mode: str = MODE_AUTO) -> Dict[str, Any]:
    """``aliases`` 是 (正确词, 误识别) 对。"""

    return {
        "id": new_scene_id(),
        "name": str(name).strip()[:MAX_NAME_CHARS] or "未命名场景",
        "mode": mode if mode in MODES else MODE_AUTO,
        "terms": list(terms or []),
        "aliases": [{"wrong": w, "right": r} for r, w in (aliases or [])],
    }


def example_scene(key: str) -> Optional[Dict[str, Any]]:
    template = EXAMPLE_SCENES.get(key)
    if template is None:
        return None
    return make_scene(template["name"], list(template["terms"]), list(template["aliases"]))


def scene_words(scene: Dict[str, Any]) -> List[str]:
    """场景里所有「正确的词」：词表加上别名指向的词。"""

    return list(dict.fromkeys([*scene["terms"], *(a["right"] for a in scene["aliases"])]))


# ───────────────────────── 设置（设置窗口写） ─────────────────────────

def default_settings() -> Dict[str, Any]:
    return {
        "enabled": True,
        "record_history": True,
        "candidate_popup": True,   # 短词不确定时在光标处弹出候选菜单
        "half_life_minutes": DEFAULT_HALF_LIFE_MINUTES,
        "scenes": [],
        "history_cleared_at": 0.0,
        "forgotten": {},   # 设置页让常驻进程忘掉的学习别名：{错词: 时间}
    }


def _clean_scene(raw: Any, seen_ids: set) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    scene_id = str(raw.get("id", "")).strip()
    if not scene_id or scene_id in seen_ids:
        scene_id = new_scene_id()
    seen_ids.add(scene_id)
    name = str(raw.get("name", "")).strip()[:MAX_NAME_CHARS] or "未命名场景"
    mode = raw.get("mode") if raw.get("mode") in MODES else MODE_AUTO
    terms_raw = raw.get("terms")
    terms = [str(t).strip() for t in terms_raw if str(t).strip()] if isinstance(terms_raw, list) else []
    aliases = []
    if isinstance(raw.get("aliases"), list):
        for item in raw["aliases"]:
            if not isinstance(item, dict):
                continue
            wrong, right = str(item.get("wrong", "")).strip(), str(item.get("right", "")).strip()
            if wrong and right and wrong != right:
                aliases.append({"wrong": wrong, "right": right})
    return {
        "id": scene_id, "name": name, "mode": mode,
        "terms": list(dict.fromkeys(terms))[:MAX_TERMS_PER_SCENE],
        "aliases": aliases[:MAX_ALIASES_PER_SCENE],
    }


def load_settings(config_root: Path) -> Dict[str, Any]:
    result = default_settings()
    raw = _read_json(Path(config_root) / SETTINGS_FILE)
    for key in ("enabled", "record_history", "candidate_popup"):
        if isinstance(raw.get(key), bool):
            result[key] = raw[key]
    try:
        half = float(raw.get("half_life_minutes", DEFAULT_HALF_LIFE_MINUTES))
        result["half_life_minutes"] = min(max(half, 1.0), 24 * 60.0)
    except (TypeError, ValueError):
        pass
    scenes = raw.get("scenes")
    if isinstance(scenes, list):
        seen: set = set()
        for item in scenes[:MAX_SCENES]:
            scene = _clean_scene(item, seen)
            if scene is not None:
                result["scenes"].append(scene)
    try:
        result["history_cleared_at"] = float(raw.get("history_cleared_at", 0.0))
    except (TypeError, ValueError):
        pass
    forgotten = raw.get("forgotten")
    if isinstance(forgotten, dict):
        for wrong, stamp in forgotten.items():
            try:
                result["forgotten"][str(wrong)] = float(stamp)
            except (TypeError, ValueError):
                continue
    return result


def save_settings(config_root: Path, settings: Dict[str, Any]) -> bool:
    return _atomic_write(Path(config_root) / SETTINGS_FILE, settings)


# ───────────────────────── 引擎（常驻进程使用） ─────────────────────────

class LexiconEngine:
    def __init__(self, config_root: Path, clock: Callable[[], float] = time.time) -> None:
        self._root = Path(config_root)
        self._clock = clock
        self._lock = threading.RLock()
        self._settings = default_settings()
        self._settings_stamp = -1.0
        self._state = self._load_state()
        self._corrector = None
        self._corrector_key = ""

    # ---- 文件
    def _load_state(self) -> Dict[str, Any]:
        raw = _read_json(self._root / STATE_FILE)
        state: Dict[str, Any] = {"heat": {}, "history": [], "learned": {}, "pending": {}, "streak": []}
        for key in state:
            if isinstance(raw.get(key), type(state[key])):
                state[key] = raw[key]
        return state

    def _save_state(self) -> None:
        _atomic_write(self._root / STATE_FILE, self._state)

    def _refresh_settings(self) -> Dict[str, Any]:
        try:
            stamp = (self._root / SETTINGS_FILE).stat().st_mtime
        except OSError:
            stamp = 0.0
        if stamp != self._settings_stamp:
            self._settings = load_settings(self._root)
            self._settings_stamp = stamp
            self._drop_forgotten()
        return self._settings

    def _drop_forgotten(self) -> None:
        changed = False
        for wrong, stamp in self._settings["forgotten"].items():
            info = self._state["learned"].get(wrong)
            if isinstance(info, dict) and float(info.get("at", 0)) <= stamp:
                self._state["learned"].pop(wrong, None)
                changed = True
        if changed:
            self._save_state()

    # ---- 热度
    def _half_life(self, settings: Dict[str, Any]) -> float:
        return float(settings["half_life_minutes"]) * 60.0

    def heat_of(self, scene_id: str, now: Optional[float] = None) -> float:
        with self._lock:
            now = self._clock() if now is None else now
            entry = self._state["heat"].get(scene_id) or {}
            return decayed(
                float(entry.get("score", 0.0)), float(entry.get("at", now)), now,
                self._half_life(self._refresh_settings()),
            )

    def active_scenes(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """现在生效的场景：开=一直生效，关=不生效，自动=按热度（带滞回）。"""

        with self._lock:
            settings = self._refresh_settings()
            if not settings["enabled"]:
                return []
            now = self._clock() if now is None else now
            half = self._half_life(settings)
            active = []
            for scene in settings["scenes"]:
                if scene["mode"] == MODE_OFF:
                    continue
                if scene["mode"] == MODE_ON:
                    active.append(scene)
                    continue
                entry = self._state["heat"].setdefault(scene["id"], {"score": 0.0, "at": now, "active": False})
                score = decayed(float(entry.get("score", 0.0)), float(entry.get("at", now)), now, half)
                was = bool(entry.get("active"))
                is_active = score >= ON_THRESHOLD or (was and score >= OFF_THRESHOLD)
                entry["active"] = is_active
                if is_active:
                    active.append(scene)
            return active

    # ---- 纠错
    def _hotword_text(self, scenes: List[Dict[str, Any]]) -> str:
        grouped: Dict[str, List[str]] = {}
        ids = {scene["id"] for scene in scenes}
        for scene in scenes:
            for term in scene["terms"]:
                grouped.setdefault(term, [])
            for item in scene["aliases"]:
                grouped.setdefault(item["right"], []).append(item["wrong"])
        for wrong, info in self._state["learned"].items():
            if isinstance(info, dict) and info.get("scene") in ids and info.get("target"):
                grouped.setdefault(str(info["target"]), []).append(wrong)
        lines = []
        for target, wrongs in grouped.items():
            seen = [part for part in dict.fromkeys(wrongs) if part != target]
            lines.append(" | ".join([target] + seen))
        return "\n".join(lines)

    def apply(self, text: str) -> str:
        """用当前生效的场景词表纠正文字；没有生效场景就原样返回。"""

        text = str(text or "")
        if not text.strip():
            return text
        try:
            with self._lock:
                scenes = self.active_scenes()
                if not scenes:
                    return text
                hot = self._hotword_text(scenes)
                if not hot:
                    return text
                key = hashlib.sha1(hot.encode("utf-8")).hexdigest()
                if key != self._corrector_key or self._corrector is None:
                    from .capswriter_hw_hot_phoneme import PhonemeCorrector

                    corrector = PhonemeCorrector(0.85, 0.6)
                    corrector.update_hotwords(hot)
                    self._corrector, self._corrector_key = corrector, key
                corrector = self._corrector
            return corrector.correct(text, k=10).text
        except Exception as exc:  # noqa: BLE001 - 词表出错不能挡住识别
            _LOGGER.warning("voice lexicon apply failed: error_type=%s", type(exc).__name__)
            return text

    # ---- 观察（每次识别完成后调用）
    def observe(self, raw: str, final: str) -> None:
        raw, final = str(raw or "").strip(), str(final or "").strip()
        if not final:
            return
        try:
            with self._lock:
                settings = self._refresh_settings()
                if not settings["enabled"]:
                    return
                now = self._clock()
                self._heat_up(final, now, settings)
                self._record(raw, final, now, settings)
                self._learn_repeat(final, now, settings)
                self._reinforce(raw, now)
                self._prune(now, settings)
                self._save_state()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("voice lexicon observe failed: error_type=%s", type(exc).__name__)

    def _heat_up(self, final: str, now: float, settings: Dict[str, Any]) -> None:
        half = self._half_life(settings)
        for scene in settings["scenes"]:
            if scene["mode"] == MODE_OFF:
                continue
            hits = sum(1 for word in scene_words(scene) if word in final)
            entry = self._state["heat"].setdefault(scene["id"], {"score": 0.0, "at": now, "active": False})
            score = decayed(float(entry.get("score", 0.0)), float(entry.get("at", now)), now, half)
            score += min(hits, HIT_CAP)
            entry.update(score=round(score, 4), at=now)

    def _record(self, raw: str, final: str, now: float, settings: Dict[str, Any]) -> None:
        if not settings["record_history"]:
            self._state["history"] = []
            return
        history = [
            item for item in self._state["history"]
            if isinstance(item, dict)
            and now - float(item.get("at", 0)) <= HISTORY_DAYS * 86400
            and float(item.get("at", 0)) > float(settings["history_cleared_at"])
        ]
        history.append({"at": now, "raw": raw, "text": final})
        self._state["history"] = history[-HISTORY_LIMIT:]

    # ---- 学习
    def _term_scene(self, settings: Dict[str, Any]) -> Dict[str, str]:
        """词 → 所属场景 id（只含没被关闭的场景）。"""

        mapping: Dict[str, str] = {}
        for scene in settings["scenes"]:
            if scene["mode"] == MODE_OFF:
                continue
            for word in scene_words(scene):
                mapping.setdefault(word, scene["id"])
        return mapping

    def _learn_repeat(self, final: str, now: float, settings: Dict[str, Any]) -> None:
        terms = self._term_scene(settings)
        streak = [
            item for item in self._state["streak"]
            if isinstance(item, dict) and now - float(item.get("at", 0)) <= REPEAT_WINDOW_SECONDS
        ]
        if final in terms:
            counted: Dict[str, int] = {}
            for item in streak:
                wrong = str(item.get("text", ""))
                if (
                    wrong and wrong != final and wrong not in terms
                    and semantic_units(wrong) <= REPEAT_MAX_UNITS
                    and pinyin_similarity(wrong, final) >= REPEAT_MIN_SIMILARITY
                ):
                    counted[wrong] = counted.get(wrong, 0) + 1
            for wrong, count in counted.items():
                self._count_candidate(wrong, final, terms[final], count, now)
            streak = []  # 这一轮重说结束
        else:
            streak.append({"text": final, "at": now})
        self._state["streak"] = streak[-STREAK_LIMIT:]

    def _count_candidate(self, wrong: str, target: str, scene_id: str, count: int, now: float) -> None:
        pending = self._state["pending"]
        entry = pending.get(wrong)
        if not isinstance(entry, dict) or entry.get("target") != target:
            entry = {"target": target, "scene": scene_id, "count": 0, "at": now}
        entry["count"] = int(entry.get("count", 0)) + count
        entry["at"] = now
        pending[wrong] = entry
        if entry["count"] >= PROMOTE_AFTER:
            self._state["learned"][wrong] = {
                "target": target, "scene": scene_id, "score": 1.5, "at": now, "hits": entry["count"],
            }
            pending.pop(wrong, None)
        for key in [k for k, v in pending.items() if now - float(v.get("at", 0)) > 3600]:
            pending.pop(key, None)

    def learn_choice(self, wrong: str, right: str) -> bool:
        """用户明确选了「wrong 其实是 right」：记成 right 所属场景下的别名。"""

        wrong, right = str(wrong or "").strip(), str(right or "").strip()
        if not wrong or not right or wrong == right:
            return False
        with self._lock:
            settings = self._refresh_settings()
            scene_id = self._term_scene(settings).get(right)
            if scene_id is None:
                return False
            self._state["learned"][wrong] = {
                "target": right, "scene": scene_id, "score": 2.0, "at": self._clock(), "hits": 1,
            }
            self._save_state()
            return True

    def forget_alias(self, wrong: str) -> bool:
        with self._lock:
            removed = self._state["learned"].pop(wrong, None) is not None
            if removed:
                self._save_state()
            return removed

    def _reinforce(self, raw: str, now: float) -> None:
        for wrong, info in self._state["learned"].items():
            if isinstance(info, dict) and wrong and wrong in raw:
                score = decayed(
                    float(info.get("score", 0)), float(info.get("at", now)), now,
                    LEARNED_HALF_LIFE_DAYS * 86400,
                )
                info.update(score=round(score + 1.0, 4), at=now, hits=int(info.get("hits", 0)) + 1)

    def _prune(self, now: float, settings: Dict[str, Any]) -> None:
        ids = {scene["id"] for scene in settings["scenes"]}
        learned = self._state["learned"]
        for wrong in list(learned):
            info = learned[wrong]
            if not isinstance(info, dict) or info.get("scene") not in ids:
                learned.pop(wrong)
                continue
            score = decayed(
                float(info.get("score", 0)), float(info.get("at", now)), now,
                LEARNED_HALF_LIFE_DAYS * 86400,
            )
            if score < LEARNED_PRUNE_BELOW:
                learned.pop(wrong)
        for scene_id in [k for k in self._state["heat"] if k not in ids]:
            self._state["heat"].pop(scene_id)

    # ---- 候选（给「这个词是哪个」菜单用）
    def popup_enabled(self) -> bool:
        with self._lock:
            settings = self._refresh_settings()
            return bool(settings["enabled"] and settings["candidate_popup"])

    def is_known_term(self, text: str) -> bool:
        """已经是某个场景里的正确词：不必再让用户选。"""

        text = str(text or "").strip()
        with self._lock:
            return text in self._term_scene(self._refresh_settings())

    def candidates(self, text: str, limit: int = 4) -> List[str]:
        """很短的词的备选：先是原词，再是生效场景里读音最像的词。长句或没有相近词返回空。"""

        text = str(text or "").strip()
        if not text or semantic_units(text) > REPEAT_MAX_UNITS:
            return []
        with self._lock:
            settings = self._refresh_settings()
            if not settings["enabled"]:
                return []
            now = self._clock()
            pool: Dict[str, float] = {}
            # 只在生效的场景里找备选：冷着的场景不该因为日常说话打扰用户
            for scene in self.active_scenes(now):
                bonus = min(self.heat_of(scene["id"], now), 5.0) / 50.0
                for word in scene_words(scene):
                    pool[word] = max(pool.get(word, 0.0), bonus)
        scored = []
        for word, bonus in pool.items():
            if word == text:
                continue
            similarity = pinyin_similarity(text, word)
            if similarity >= CANDIDATE_MIN_SIMILARITY:
                scored.append((similarity + bonus, word))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        picked = [word for _score, word in scored[: max(0, limit - 1)]]
        return [text] + picked if picked else []


# ───────────────────────── 设置窗口读取 ─────────────────────────

def read_overview(config_root: Path, now: Optional[float] = None) -> Dict[str, Any]:
    """设置页用：各场景当前热度、生效与否、学到的别名、最近识别。"""

    now = time.time() if now is None else now
    settings = load_settings(config_root)
    state = _read_json(Path(config_root) / STATE_FILE)
    half = float(settings["half_life_minutes"]) * 60.0
    heat = state.get("heat") if isinstance(state.get("heat"), dict) else {}
    learned = state.get("learned") if isinstance(state.get("learned"), dict) else {}
    scenes = []
    for scene in settings["scenes"]:
        entry = heat.get(scene["id"]) if isinstance(heat.get(scene["id"]), dict) else {}
        score = decayed(float(entry.get("score", 0.0)), float(entry.get("at", now)), now, half)
        if not settings["enabled"] or scene["mode"] == MODE_OFF:
            active = False
        elif scene["mode"] == MODE_ON:
            active = True
        else:
            active = score >= ON_THRESHOLD or (bool(entry.get("active")) and score >= OFF_THRESHOLD)
        scenes.append({
            "id": scene["id"], "name": scene["name"], "mode": scene["mode"],
            "heat": round(score, 2), "active": active,
            "termsText": "，".join(scene["terms"]),
            "aliases": [dict(a) for a in scene["aliases"]],
            "learned": [
                {"wrong": wrong, "right": str(info.get("target", ""))}
                for wrong, info in learned.items()
                if isinstance(info, dict) and info.get("scene") == scene["id"]
                and float(info.get("at", 0)) > settings["forgotten"].get(wrong, 0.0)
            ],
        })
    history = state.get("history") if isinstance(state.get("history"), list) else []
    cutoff = float(settings["history_cleared_at"])
    return {
        "settings": settings,
        "scenes": scenes,
        "history": [
            {"at": float(item.get("at", 0)), "raw": str(item.get("raw", "")), "text": str(item.get("text", ""))}
            for item in history
            if isinstance(item, dict) and float(item.get("at", 0)) > cutoff
        ][-30:][::-1],
    }
