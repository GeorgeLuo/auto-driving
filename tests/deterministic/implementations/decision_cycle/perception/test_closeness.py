"""Closeness calls the nearest box in each band near when the car would reach the stop row within one usual run."""

import unittest
from dataclasses import replace

from autonomy.decision_cycle.memory.beliefs import Belief
from autonomy.decision_cycle.memory.publication import belief_key

from autonomy.decision_cycle.perception.evidence.values import PerceivedThing, ViewLocation
from autonomy.decision_cycle.perception.plugin import PerceptionPluginInputs
from implementations.decision_cycle.perception.plugins.closeness.plugin import ClosenessPlugin

MODEL_KEY = belief_key("approach_model.ms_per_row")
RUN_KEY = belief_key("approach_model.run_ms")
STOP_ROW_KEY = belief_key("stop_row.row")


def _box(bbox, *, plugin_id="sim_cardboard_obstacle", kind="obstruction_evidence", index=0):
    return PerceivedThing(
        thing_id=f"box_{index}", kind=kind, label="cardboard",
        location=ViewLocation(frame="front_camera", zone="image", bbox_xyxy_norm=bbox),
        source_plugin_id=plugin_id,
    )


def _memory(ms_per_row=None, *, run_ms=250, stop_row=0.6, row_bins=10):
    """Ten rows: row 4 (0.4-0.5) takes 300 ms and row 5 (0.5-0.6) takes 100 ms unless given."""

    return {
        MODEL_KEY: Belief("approach_model.ms_per_row", {"4": 300, "5": 100} if ms_per_row is None else ms_per_row,
                          "approach_model", 1, properties={"row_bins": row_bins}),
        RUN_KEY: Belief("approach_model.run_ms", run_ms, "approach_model", 1),
        STOP_ROW_KEY: Belief("stop_row.row", stop_row, "stop_row", 1),
    }


def _signals(*things, memory=None, plugin=None):
    batch = (plugin or ClosenessPlugin()).perceive(PerceptionPluginInputs(
        frame_id="frame_1", captured_at_ms=10, feeds={}, diagnostics=None,
        shared_memory=_memory() if memory is None else memory, prior_things=things,
    ))
    return {signal.signal_id: signal for signal in batch.signals}


def _ahead(bottom, memory=None):
    signal = _signals(_box((0.4, 0.2, 0.6, bottom)), memory=memory)["closeness_ahead"]
    return signal.value, signal.properties["reach_ms"], signal.properties["reason"]


class ClosenessTests(unittest.TestCase):
    def test_nothing_in_a_band_has_no_closeness(self):
        signals = _signals()
        self.assertEqual(set(signals), {"closeness_ahead", "closeness_left", "closeness_right"})
        for signal in signals.values():
            self.assertIsNone(signal.value)
            self.assertEqual(signal.properties["reason"], "nothing_in_band")
            self.assertIsNone(signal.properties["row"])

    def test_a_box_is_near_when_its_reach_is_within_one_run(self):
        self.assertEqual(_ahead(0.5), ("near", 100, "within_one_run"))
        self.assertEqual(_ahead(0.45), ("near", 250, "within_one_run"))
        self.assertEqual(_ahead(0.4), ("far", 400, "beyond_one_run"))
        self.assertEqual(_ahead(0.7), ("near", 0, "within_one_run"))
        signal = _signals(_box((0.4, 0.2, 0.6, 0.45)))["closeness_ahead"]
        self.assertEqual(signal.properties, {
            "min_x": 0.3, "max_x": 0.7, "row": 0.45, "stop_row": 0.6, "run_ms": 250.0,
            "reach_ms": 250, "reason": "within_one_run",
        })

    def test_each_band_reads_its_own_nearest_box(self):
        signals = _signals(_box((0.4, 0.2, 0.6, 0.4), index=0), _box((0.35, 0.2, 0.5, 0.52), index=1),
                           _box((0.72, 0.2, 0.9, 0.3), index=2))
        self.assertEqual((signals["closeness_ahead"].value, signals["closeness_ahead"].properties["row"]),
                         ("near", 0.52))
        self.assertEqual((signals["closeness_right"].value, signals["closeness_right"].properties["row"]),
                         ("far", 0.3))
        self.assertEqual(signals["closeness_left"].properties["reason"], "nothing_in_band")
        self.assertEqual((signals["closeness_left"].properties["min_x"], signals["closeness_left"].properties["max_x"]),
                         (0.0, 0.35))
        right_only = _signals(_box((0.7, 0.2, 0.9, 0.55)))
        self.assertEqual(right_only["closeness_ahead"].properties["reason"], "nothing_in_band")
        self.assertEqual(right_only["closeness_right"].value, "near")
        left_only = _signals(_box((0.05, 0.2, 0.3, 0.4)))
        self.assertEqual([left_only[name].value for name in ("closeness_ahead", "closeness_left", "closeness_right")],
                         [None, "far", None])

    def test_only_detection_boxes_count(self):
        for thing in (_box((0.4, 0.2, 0.6, 0.55), plugin_id="sim_cardboard_permanence"),
                      _box((0.4, 0.2, 0.6, 0.55), kind="landmark"),
                      _box(None)):
            with self.subTest(thing=thing):
                self.assertIsNone(_signals(thing)["closeness_ahead"].value)
        self.assertEqual(_signals(_box((0.4, 0.2, 0.6, 0.55), plugin_id="sim_wall"))["closeness_ahead"].value,
                         "near")
        walls_only = ClosenessPlugin(detection_plugin_ids=["sim_wall"])
        self.assertIsNone(_signals(_box((0.4, 0.2, 0.6, 0.55)), plugin=walls_only)["closeness_ahead"].value)

    def test_an_unlearned_row_borrows_its_learned_neighbours_or_leaves_the_box_unknown(self):
        self.assertEqual(_ahead(0.4, _memory({"3": 300, "5": 100}))[:2], ("far", 300))
        self.assertEqual(_ahead(0.5, _memory({"4": 120}))[:2], ("near", 120))
        self.assertEqual(_ahead(0.5, _memory({"4": 120}, stop_row=0.65)), (None, 120, "not_learned"))
        self.assertEqual(_ahead(0.3, _memory({"5": 100})), (None, 200, "not_learned"))
        self.assertEqual(_ahead(0.3, _memory({"5": 100}, run_ms=150)), ("far", 200, "beyond_one_run"))

    def test_without_a_stop_row_a_run_or_rows_the_box_is_unknown(self):
        for memory in (
            {}, _memory(stop_row=None), _memory(run_ms=None), _memory(run_ms=True), _memory(run_ms=0),
            _memory(row_bins=None), _memory(row_bins=True), {MODEL_KEY: "learned", STOP_ROW_KEY: [0.6]},
        ):
            with self.subTest(memory=memory):
                self.assertEqual(_ahead(0.5, memory), (None, None, "not_learned"))
        self.assertEqual(_ahead(0.5, _memory({"x": 50, "5": -1, "6": True})), (None, 0, "not_learned"))

    def test_memory_keys_are_configurable_and_required(self):
        plugin = ClosenessPlugin(model_belief_id="model", stop_row_belief_id="stop")
        memory = {belief_key("model"): replace(_memory()[MODEL_KEY], belief_id="model"),
                  RUN_KEY: _memory()[RUN_KEY],
                  belief_key("stop"): Belief("stop", .6, "stop_row", 1)}
        self.assertEqual(_signals(_box((0.4, 0.2, 0.6, 0.5)), memory=memory, plugin=plugin)["closeness_ahead"].value,
                         "near")
        with self.assertRaises(ValueError):
            ClosenessPlugin().perceive(PerceptionPluginInputs(
                frame_id="frame_1", captured_at_ms=10, feeds={}, diagnostics=None, prior_things=(),
            ))

    def test_invalid_configuration_is_rejected(self):
        for config in (
            {"detection_plugin_ids": []}, {"detection_plugin_ids": "sim_wall"}, {"detection_plugin_ids": [""]},
            {"model_belief_id": ""}, {"stop_row_belief_id": None}, {"ahead_min_x": -0.1}, {"ahead_max_x": 1.5},
            {"ahead_min_x": 0.7, "ahead_max_x": 0.3}, {"right_min_x": 1.0}, {"right_min_x": float("nan")},
            {"ahead_min_x": True}, {"left_max_x": 0}, {"left_max_x": -0.1}, {"left_max_x": 1.5},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                ClosenessPlugin(**config)
