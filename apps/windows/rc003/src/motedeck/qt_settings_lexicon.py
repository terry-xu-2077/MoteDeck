"""VoiceLexiconController：语音页「场景词表」的 QML 适配层。

只做两件事：读常驻进程写的状态（热度、学到的别名、最近识别）来显示；把用户的
选择写进设置文件（场景、别名、开关、半衰期、忘记某条学到的别名、清空历史）。两个
进程各写各的文件，见 voice_lexicon 模块说明。
"""

from __future__ import annotations

import time
from pathlib import Path

from . import voice_lexicon as lex
from .qt_settings_qml_tokens import Property, QObject, QTimer, Signal, Slot

_POLL_MS = 2000


class VoiceLexiconController(QObject):
    changed = Signal()

    def __init__(self, config_root, parent=None) -> None:
        super().__init__(parent)
        self._root = Path(config_root)
        self._overview = lex.read_overview(self._root)
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self.refresh)

    # -- 读 --
    enabled = Property(bool, lambda self: bool(self._overview["settings"]["enabled"]), notify=changed)
    recordHistory = Property(
        bool, lambda self: bool(self._overview["settings"]["record_history"]), notify=changed
    )
    candidatePopup = Property(
        bool, lambda self: bool(self._overview["settings"]["candidate_popup"]), notify=changed
    )
    halfLifeMinutes = Property(
        float, lambda self: float(self._overview["settings"]["half_life_minutes"]), notify=changed
    )
    scenes = Property("QVariantList", lambda self: list(self._overview["scenes"]), notify=changed)

    @Property("QVariantList", notify=changed)
    def exampleScenes(self):
        """还没导入的示例场景（按名字判断）：[{key, name, count}]。"""

        names = {scene["name"] for scene in self._overview["scenes"]}
        return [
            {"key": key, "name": spec["name"], "count": len(spec["terms"])}
            for key, spec in lex.EXAMPLE_SCENES.items()
            if spec["name"] not in names
        ]

    def _get_history(self):
        rows = []
        for item in self._overview["history"]:
            stamp = time.strftime("%H:%M:%S", time.localtime(item["at"])) if item["at"] else ""
            rows.append({"time": stamp, "text": item["text"], "raw": item["raw"],
                         "corrected": item["raw"] != item["text"]})
        return rows

    history = Property("QVariantList", _get_history, notify=changed)

    # -- 轮询（页面可见时才跑） --
    @Slot()
    def start(self) -> None:
        self.refresh()
        if not self._timer.isActive():
            self._timer.start()

    @Slot()
    def stop(self) -> None:
        self._timer.stop()

    @Slot()
    def refresh(self) -> None:
        overview = lex.read_overview(self._root)
        if overview != self._overview:
            self._overview = overview
            self.changed.emit()

    # -- 写（只改设置文件） --
    def _update(self, mutate) -> None:
        settings = lex.load_settings(self._root)
        mutate(settings)
        lex.save_settings(self._root, settings)
        self.refresh()

    @staticmethod
    def _scene(settings, scene_id):
        return next((s for s in settings["scenes"] if s["id"] == scene_id), None)

    @Slot(bool)
    def setEnabled(self, value: bool) -> None:
        self._update(lambda s: s.__setitem__("enabled", bool(value)))

    @Slot(bool)
    def setRecordHistory(self, value: bool) -> None:
        self._update(lambda s: s.__setitem__("record_history", bool(value)))

    @Slot(bool)
    def setCandidatePopup(self, value: bool) -> None:
        self._update(lambda s: s.__setitem__("candidate_popup", bool(value)))

    @Slot(float)
    def setHalfLifeMinutes(self, minutes: float) -> None:
        self._update(lambda s: s.__setitem__("half_life_minutes", float(minutes)))

    @Slot()
    def clearHistory(self) -> None:
        self._update(lambda s: s.__setitem__("history_cleared_at", time.time()))

    # 场景
    @Slot(str, result=bool)
    def addScene(self, name: str) -> bool:
        name = name.strip()
        if not name:
            return False
        created = []

        def mutate(s):
            if len(s["scenes"]) < lex.MAX_SCENES:
                s["scenes"].append(lex.make_scene(name))
                created.append(True)

        self._update(mutate)
        return bool(created)

    @Slot(str, result=bool)
    def importExample(self, key: str) -> bool:
        scene = lex.example_scene(key)
        if scene is None:
            return False
        names = {s["name"] for s in self._overview["scenes"]}
        if scene["name"] in names:
            return False
        self._update(lambda s: s["scenes"].append(scene) if len(s["scenes"]) < lex.MAX_SCENES else None)
        return True

    @Slot(str)
    def removeScene(self, scene_id: str) -> None:
        self._update(lambda s: s.__setitem__("scenes", [x for x in s["scenes"] if x["id"] != scene_id]))

    @Slot(str, str)
    def renameScene(self, scene_id: str, name: str) -> None:
        name = name.strip()[: lex.MAX_NAME_CHARS]
        if not name:
            self.refresh()
            return

        def mutate(s):
            scene = self._scene(s, scene_id)
            if scene is not None:
                scene["name"] = name

        self._update(mutate)

    @Slot(str, str)
    def setSceneMode(self, scene_id: str, mode: str) -> None:
        if mode not in lex.MODES:
            return

        def mutate(s):
            scene = self._scene(s, scene_id)
            if scene is not None:
                scene["mode"] = mode

        self._update(mutate)

    @Slot(str, str)
    def setSceneTerms(self, scene_id: str, text: str) -> None:
        terms = lex.split_terms(text)[: lex.MAX_TERMS_PER_SCENE]

        def mutate(s):
            scene = self._scene(s, scene_id)
            if scene is not None:
                scene["terms"] = terms

        self._update(mutate)

    # 场景里的别名
    @Slot(str, str, str, result=bool)
    def addSceneAlias(self, scene_id: str, wrong: str, right: str) -> bool:
        wrong, right = wrong.strip(), right.strip()
        if not wrong or not right or wrong == right:
            return False
        done = []

        def mutate(s):
            scene = self._scene(s, scene_id)
            if scene is None or len(scene["aliases"]) >= lex.MAX_ALIASES_PER_SCENE:
                return
            scene["aliases"] = [a for a in scene["aliases"] if a["wrong"] != wrong]
            scene["aliases"].append({"wrong": wrong, "right": right})
            done.append(True)

        self._update(mutate)
        return bool(done)

    @Slot(str, str)
    def removeSceneAlias(self, scene_id: str, wrong: str) -> None:
        def mutate(s):
            scene = self._scene(s, scene_id)
            if scene is not None:
                scene["aliases"] = [a for a in scene["aliases"] if a["wrong"] != wrong]

        self._update(mutate)

    @Slot(str)
    def forgetLearned(self, wrong: str) -> None:
        self._update(lambda s: s["forgotten"].__setitem__(wrong, time.time()))
