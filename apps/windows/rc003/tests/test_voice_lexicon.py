"""语音场景词表：用户自建场景、热度/冷却、场景内纠错、重说学习、候选和容错。"""

import json
import math
import tempfile
import unittest
from pathlib import Path

from motedeck import voice_lexicon as lex


class _Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def save_scene(root, mode="auto", scene=None, **extra):
    """把一个场景（默认是分镜示例）写进设置文件，返回场景。"""

    scene = scene or lex.example_scene("film")
    scene["id"] = scene.get("id") or "s1"
    scene["id"] = "s1" if scene["id"] != "s2" else "s2"
    scene["mode"] = mode
    settings = lex.load_settings(root)
    settings["scenes"] = [s for s in settings["scenes"] if s["id"] != scene["id"]] + [scene]
    settings.update(extra)
    lex.save_settings(root, settings)
    return scene


class LexiconTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.clock = _Clock()
        save_scene(self.root)
        self.engine = lex.LexiconEngine(self.root, clock=self.clock)

    def say(self, text, gap=3):
        """模拟一次识别：纠错 → 观察，返回最终文字。"""

        self.clock.advance(gap)
        final = self.engine.apply(text)
        self.engine.observe(text, final)
        return final

    def heat_up(self):
        for _ in range(3):
            self.say("镜头推进，近景，特写")

    # ---- 没有场景就什么都不做
    def test_without_scenes_nothing_is_touched(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: None)
        engine = lex.LexiconEngine(root, clock=self.clock)
        self.assertEqual(engine.apply("静静"), "静静")
        self.assertEqual(engine.candidates("静静"), [])
        self.assertEqual(engine.active_scenes(), [])

    # ---- 热度与冷却
    def test_decay_halves_each_half_life(self):
        self.assertAlmostEqual(lex.decayed(4.0, 0, 1800, 1800), 2.0)
        self.assertAlmostEqual(lex.decayed(4.0, 0, 3600, 1800), 1.0)
        self.assertEqual(lex.decayed(4.0, 0, 10, 0), 0.0)

    def test_cold_scene_does_not_touch_everyday_speech(self):
        self.assertEqual(self.say("我安安静静地坐着"), "我安安静静地坐着")
        self.assertEqual(self.say("静静"), "静静")
        self.assertEqual(self.engine.active_scenes(), [])

    def test_talking_in_a_scene_heats_it_and_enables_its_aliases(self):
        self.heat_up()
        self.assertEqual([s["id"] for s in self.engine.active_scenes()], ["s1"])
        self.assertEqual(self.say("静静"), "近景")
        self.assertEqual(self.say("院景"), "远景")

    def test_scene_cools_down_after_silence(self):
        self.heat_up()
        self.assertTrue(self.engine.active_scenes())
        self.clock.advance(3 * 3600)
        self.assertEqual(self.engine.active_scenes(), [])
        self.assertEqual(self.say("静静"), "静静")

    def test_hysteresis_keeps_the_scene_until_it_is_clearly_cold(self):
        self.heat_up()
        heat = self.engine.heat_of("s1")
        self.assertGreater(heat, lex.ON_THRESHOLD)
        half = lex.DEFAULT_HALF_LIFE_MINUTES * 60
        wait = half * math.log2(heat / ((lex.ON_THRESHOLD + lex.OFF_THRESHOLD) / 2))
        self.clock.advance(wait)
        self.assertTrue(self.engine.active_scenes())
        self.clock.advance(half * 2)
        self.assertEqual(self.engine.active_scenes(), [])

    def test_one_sentence_cannot_flood_the_score(self):
        self.say("近景近景近景近景近景近景近景近景特写特写特写")
        self.assertLessEqual(self.engine.heat_of("s1"), lex.HIT_CAP + 0.001)

    def test_modes_force_on_and_off(self):
        save_scene(self.root, "on")
        self.assertEqual(self.say("静静"), "近景")
        save_scene(self.root, "off")
        self.heat_up()
        self.assertEqual(self.say("静静"), "静静")
        settings = lex.load_settings(self.root)
        settings["enabled"] = False
        lex.save_settings(self.root, settings)
        save_scene(self.root, "on", enabled=False)
        self.assertEqual(self.say("静静"), "静静")

    # ---- 场景互相隔离
    def test_a_scene_alias_never_leaks_into_another_scene(self):
        other = lex.make_scene("编程", ["函数", "变量", "接口"], [("接口", "借口")])
        other["id"] = "s2"
        save_scene(self.root, "auto", other)
        for _ in range(3):
            self.say("定义一个函数和变量，调用接口")
        self.assertEqual([s["id"] for s in self.engine.active_scenes()], ["s2"])
        self.assertEqual(self.say("借口"), "接口")      # 编程场景热：它的别名生效
        self.assertEqual(self.say("静静"), "静静")       # 分镜场景冷：它的别名不生效
        self.clock.advance(5 * 3600)
        self.assertEqual(self.say("借口"), "借口")       # 编程也冷了：别名不再生效

    def test_two_scenes_can_be_hot_at_once(self):
        other = lex.make_scene("编程", ["函数", "变量", "接口"])
        other["id"] = "s2"
        save_scene(self.root, "auto", other)
        for _ in range(3):
            self.say("镜头推进，近景，特写")
            self.say("定义一个函数和变量，调用接口")
        self.assertEqual({s["id"] for s in self.engine.active_scenes()}, {"s1", "s2"})

    # ---- 重说学习
    def test_repeating_until_right_teaches_an_alias_that_cools_with_disuse(self):
        save_scene(self.root, "on")
        for _ in range(4):
            self.clock.advance(3)
            self.engine.observe("近井", "近井")
        self.clock.advance(3)
        self.engine.observe("近景", "近景")
        overview = lex.read_overview(self.root, self.clock())
        learned = overview["scenes"][0]["learned"]
        self.assertEqual(learned, [{"wrong": "近井", "right": "近景"}])
        self.assertEqual(self.engine.apply("近井"), "近景")
        self.clock.advance(60 * 86400)
        self.engine.observe("今天天气不错", "今天天气不错")
        self.assertEqual(lex.read_overview(self.root, self.clock())["scenes"][0]["learned"], [])

    def test_learned_alias_belongs_to_the_scene_of_the_corrected_word(self):
        other = lex.make_scene("编程", ["函数"])
        other["id"] = "s2"
        save_scene(self.root, "on", other)
        for _ in range(3):
            self.clock.advance(3)
            self.engine.observe("近井", "近井")
        self.clock.advance(3)
        self.engine.observe("近景", "近景")
        scenes = {s["id"]: s for s in lex.read_overview(self.root, self.clock())["scenes"]}
        self.assertEqual(len(scenes["s1"]["learned"]), 1)
        self.assertEqual(scenes["s2"]["learned"], [])

    def test_unrelated_short_phrases_are_never_learned(self):
        save_scene(self.root, "on")
        for _ in range(4):
            self.say("好的")
        self.say("近景")
        self.assertEqual(lex.read_overview(self.root, self.clock())["scenes"][0]["learned"], [])

    def test_a_single_slip_is_not_enough(self):
        save_scene(self.root, "on")
        self.clock.advance(3)
        self.engine.observe("近井", "近井")
        self.clock.advance(3)
        self.engine.observe("近景", "近景")
        self.assertEqual(lex.read_overview(self.root, self.clock())["scenes"][0]["learned"], [])

    def test_choice_is_learned_immediately_and_can_be_forgotten(self):
        save_scene(self.root, "on")
        self.assertTrue(self.engine.learn_choice("苹果", "近景"))
        self.assertEqual(self.say("苹果"), "近景")
        self.assertTrue(self.engine.forget_alias("苹果"))
        self.assertEqual(self.say("苹果"), "苹果")
        self.assertFalse(self.engine.learn_choice("近景", "近景"))
        self.assertFalse(self.engine.learn_choice("苹果", "不在任何场景里的词"))

    def test_settings_window_can_make_the_engine_forget_a_learned_alias(self):
        save_scene(self.root, "on")
        self.engine.learn_choice("苹果", "近景")
        settings = lex.load_settings(self.root)
        settings["forgotten"]["苹果"] = self.clock() + 1
        lex.save_settings(self.root, settings)
        self.clock.advance(5)
        self.assertEqual(self.say("苹果"), "苹果")

    # ---- 场景内别名
    def test_user_alias_in_a_scene_applies_only_while_the_scene_is_active(self):
        scene = lex.make_scene("品牌", ["Claude"], [("Claude", "克劳得")])
        scene["id"] = "s2"
        save_scene(self.root, "auto", scene)
        self.assertEqual(self.say("克劳得"), "克劳得")  # 还没热
        save_scene(self.root, "on", scene)
        self.assertEqual(self.say("克劳得"), "Claude")

    # ---- 候选
    def test_candidates_are_close_terms_with_the_original_first(self):
        self.heat_up()
        found = self.engine.candidates("静静")
        self.assertEqual(found[0], "静静")
        self.assertIn("近景", found)
        self.assertLessEqual(len(found), 4)

    def test_cold_scenes_never_offer_candidates(self):
        self.assertEqual(self.engine.candidates("静静"), [])

    def test_long_text_and_unrelated_words_have_no_candidates(self):
        self.heat_up()
        self.assertEqual(self.engine.candidates("我今天想去公园散步"), [])
        self.assertEqual(self.engine.candidates("天气"), [])

    def test_known_terms_and_the_popup_switch(self):
        self.assertTrue(self.engine.is_known_term("近景"))
        self.assertFalse(self.engine.is_known_term("静静"))
        self.assertTrue(self.engine.popup_enabled())
        settings = lex.load_settings(self.root)
        settings["candidate_popup"] = False
        lex.save_settings(self.root, settings)
        self.assertFalse(self.engine.popup_enabled())

    # ---- 历史与隐私
    def test_history_is_recorded_capped_and_clearable(self):
        for index in range(lex.HISTORY_LIMIT + 20):
            self.say(f"第{index}句")
        state = json.loads((self.root / lex.STATE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(len(state["history"]), lex.HISTORY_LIMIT)
        settings = lex.load_settings(self.root)
        settings["history_cleared_at"] = self.clock()
        lex.save_settings(self.root, settings)
        self.assertEqual(lex.read_overview(self.root, self.clock())["history"], [])

    def test_turning_history_off_stops_recording_but_keeps_heat(self):
        settings = lex.load_settings(self.root)
        settings["record_history"] = False
        lex.save_settings(self.root, settings)
        self.heat_up()
        state = json.loads((self.root / lex.STATE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(state["history"], [])
        self.assertTrue(self.engine.active_scenes())

    # ---- 容错与持久
    def test_state_survives_restart_and_keeps_cooling(self):
        self.heat_up()
        again = lex.LexiconEngine(self.root, clock=self.clock)
        self.assertTrue(again.active_scenes())
        self.clock.advance(3 * 3600)
        self.assertEqual(again.active_scenes(), [])

    def test_deleting_a_scene_drops_its_heat_and_learned_aliases(self):
        save_scene(self.root, "on")
        self.engine.learn_choice("苹果", "近景")
        settings = lex.load_settings(self.root)
        settings["scenes"] = []
        lex.save_settings(self.root, settings)
        self.say("随便说点什么")
        state = json.loads((self.root / lex.STATE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(state["learned"], {})
        self.assertEqual(state["heat"], {})

    def test_corrupt_files_fall_back_to_defaults(self):
        (self.root / lex.SETTINGS_FILE).write_text("{not json", encoding="utf-8")
        (self.root / lex.STATE_FILE).write_text("[1,2,3]", encoding="utf-8")
        engine = lex.LexiconEngine(self.root, clock=self.clock)
        self.assertEqual(engine.apply("近景"), "近景")
        engine.observe("近景", "近景")
        self.assertEqual(lex.load_settings(self.root)["scenes"], [])

    def test_settings_are_sanitised(self):
        (self.root / lex.SETTINGS_FILE).write_text(
            json.dumps({
                "half_life_minutes": 99999, "enabled": "yes",
                "scenes": [
                    {"id": "a", "name": "x" * 99, "mode": "sideways", "terms": ["甲", "", "甲", 5],
                     "aliases": [{"wrong": "a", "right": "a"}, {"wrong": "", "right": "b"}, 5,
                                 {"wrong": "x", "right": "y"}]},
                    {"id": "a", "name": "重复 id"},
                    "not a scene",
                ],
            }),
            encoding="utf-8",
        )
        settings = lex.load_settings(self.root)
        self.assertEqual(settings["half_life_minutes"], 24 * 60.0)
        self.assertTrue(settings["enabled"])
        first, second = settings["scenes"]
        self.assertEqual(len(first["name"]), lex.MAX_NAME_CHARS)
        self.assertEqual(first["mode"], "auto")
        self.assertEqual(first["terms"], ["甲", "5"])
        self.assertEqual(first["aliases"], [{"wrong": "x", "right": "y"}])
        self.assertNotEqual(first["id"], second["id"])

    def test_split_terms_accepts_common_separators(self):
        self.assertEqual(lex.split_terms("近景，远景、特写;全景\n中景, 近景"), ["近景", "远景", "特写", "全景", "中景"])

    def test_the_examples_are_normal_editable_scenes_with_fresh_ids(self):
        first, second = lex.example_scene("film"), lex.example_scene("film")
        self.assertNotEqual(first["id"], second["id"])
        self.assertIn("近景", first["terms"])
        self.assertIn({"wrong": "静静", "right": "近景"}, first["aliases"])
        self.assertIsNone(lex.example_scene("nope"))

    def test_there_are_film_programming_and_design_examples_of_about_a_hundred_terms(self):
        self.assertEqual(set(lex.EXAMPLE_SCENES), {"film", "programming", "design"})
        for key in lex.EXAMPLE_SCENES:
            scene = lex.example_scene(key)
            self.assertGreaterEqual(len(scene["terms"]), 100, key)
            self.assertEqual(len(scene["terms"]), len(set(scene["terms"])), key)
            self.assertTrue(scene["aliases"], key)
            for alias in scene["aliases"]:
                self.assertNotEqual(alias["wrong"], alias["right"])
                self.assertIn(alias["right"], scene["terms"], f"{key}: {alias}")


class PostprocessorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config = Path(self._tmp.name) / "cfg"
        self.config.mkdir()
        self.cw = Path(self._tmp.name) / "cw"
        self.cw.mkdir()
        (self.cw / "hot.txt").write_text("Claude | 克劳得" + chr(10), encoding="utf-8")
        save_scene(self.config)
        self.clock = _Clock()
        from motedeck import capswriter_postprocess as post

        self.engine = lex.LexiconEngine(self.config, clock=self.clock)
        self.post = post.Postprocessor(lexicon=self.engine)

    def test_lexicon_runs_after_hot_txt_and_observes_every_result(self):
        self.assertEqual(self.post.process("克劳得", self.cw), "Claude")
        for _ in range(3):
            self.clock.advance(2)
            self.post.process("镜头推进，近景，特写", self.cw)
        self.clock.advance(2)
        self.assertEqual(self.post.process("静静", self.cw), "近景")
        state = json.loads((self.config / lex.STATE_FILE).read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(state["history"]), 5)

    def test_works_without_a_capswriter_folder(self):
        for _ in range(3):
            self.clock.advance(2)
            self.post.process("镜头推进，近景，特写", None)
        self.assertEqual(self.post.process("静静", None), "近景")

    def test_no_lexicon_keeps_the_old_behaviour(self):
        from motedeck import capswriter_postprocess as post

        plain = post.Postprocessor()
        self.assertEqual(plain.process("克劳得", self.cw), "Claude")
        self.assertIsNone(plain.lexicon)


if __name__ == "__main__":
    unittest.main()
