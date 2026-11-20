"""Constructed-array checks for floor scans, exposed settings, and fusion cues."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
from PIL import Image

from autonomy.decision_cycle.context import DecisionFrameContext
from autonomy.decision_cycle.memory.evidence import MemoryOrigin, RetainedEvidence
from autonomy.decision_cycle.memory.publication import EVIDENCE_KEY
from autonomy.decision_cycle.perception.evidence.values import ViewLocation
from autonomy.decision_cycle.perception.plugin import PerceptionPlugin, PerceptionPluginInputs
from autonomy.fields import FieldError
from implementations.decision_cycle.catalog import preset_activation, step_plugins
from implementations.decision_cycle.perception.feeds.camera import CameraFrame
from implementations.decision_cycle.perception.plugins.composite_box_fusion.issue219_cues import (
    PreparedCueImage,
    _corner_junctions,
    _integral_mean,
    _keep_scored_boxes,
    _normalized_xywh,
)
from implementations.decision_cycle.perception.plugins.composite_box_fusion.plugin import (
    CompositeBoxFusionPlugin,
    _polygon_xy_norm,
)
from implementations.decision_cycle.perception.plugins.floor_continuity.model import (
    FloorContinuityAnalysis,
    FloorContinuityConfig,
    _first_interruptions,
)
from implementations.decision_cycle.perception.plugins.floor_continuity.plugin import FloorContinuityPlugin
from implementations.decision_cycle.perception.plugins.floor_continuity_temporal.plugin import (
    TemporalFloorContinuityPlugin,
)
from implementations.decision_cycle.perception.plugins.floor_plane.model import (
    FloorPlaneConfig,
    estimate_floor_mask,
    process_still,
    source_obstacle_hits,
)
from implementations.decision_cycle.perception.plugins.floor_plane.plugin import FloorPlanePlugin
from implementations.decision_cycle.perception.plugins.multi_obstruction_tracks.plugin import (
    MultiObstructionTracksPlugin,
)
from implementations.decision_cycle.perception.plugins.sim_cardboard_obstacle.plugin import (
    SimCardboardObstaclePlugin,
    _cardboard_mask,
)
from implementations.decision_cycle.perception.plugins.sim_color_targets.plugin import (
    SimColorTargetsPlugin,
    _largest_component,
)
from implementations.decision_cycle.perception.shared.features.feature_tracking import (
    FeatureMatch,
    detect_keypoints,
)
from implementations.decision_cycle.perception.shared.floor.scan import first_column_hits
from implementations.decision_cycle.perception.shared.motion.scene_motion import (
    find_best_similarity_group,
)
from implementations.decision_cycle.proposal.plugins.avoid_recent_obstruction.plugin import (
    AvoidRecentObstruction,
    DEFAULT_FORWARD_THROTTLE,
)
from scripts.perception.process_still import main as process_still_main


def _catalog_config(plugin_id: str) -> dict:
    return step_plugins("perception")[plugin_id]["default_config"]


class FloorPlaneTests(unittest.TestCase):
    def test_flat_override_reaches_the_config_and_bad_values_fail(self) -> None:
        plugin = FloorPlanePlugin(horizon_ratio=0.45)
        self.assertEqual(plugin.config.horizon_ratio, 0.45)
        self.assertIsInstance(plugin.config, FloorPlaneConfig)
        self.assertIsInstance(plugin, PerceptionPlugin)

        with self.assertRaises(TypeError):
            FloorPlanePlugin(not_a_floor_field=1)
        for bad_config in (
            {"horizon_ratio": "low"},
            {"horizon_ratio": -0.1},
            {"horizon_ratio": 1.1},
            {"morph_radius": 1.5},
            {"seed_x0_ratio": 0.75, "seed_x1_ratio": 0.25},
            {"seed_x0_ratio": 0.5, "seed_x1_ratio": 0.5},
            {"seed_y0_ratio": 0.9, "seed_y1_ratio": 0.7},
            {"topdown_width": 1},
            {"topdown_height": 0},
            {"obstacle_run_length": 0},
            {"min_floor_support": 0},
            {"floor_threshold": -1.0},
            {"working_width": -1},
            {"working_width": 1.5},
        ):
            with self.subTest(bad_config=bad_config), self.assertRaises(FieldError):
                FloorPlanePlugin(**bad_config)

    def test_working_width_downscales_wide_frames_and_preserves_source_dimensions_and_bboxes(self) -> None:
        rgb = np.full((480, 640, 3), (120, 100, 80), dtype=np.uint8)
        rgb[:260] = (20, 90, 160)
        rgb[260:325, 230:390] = (180, 60, 40)
        frame = CameraFrame("front_camera", 0, rgb)
        inputs = PerceptionPluginInputs("test-frame", 0, {"frame": frame})
        with mock.patch("implementations.decision_cycle.perception.plugins.floor_plane.plugin.cv2.resize", wraps=cv2.resize) as resize:
            downscaled = FloorPlanePlugin().perceive(inputs).measurements
            self.assertEqual(resize.call_args.args[1], (320, 240))
            full = FloorPlanePlugin(working_width=0).perceive(inputs).measurements
            self.assertEqual(resize.call_count, 1)
        self.assertEqual((downscaled["image_width_px"], downscaled["image_height_px"]), (640, 480))
        self.assertTrue(downscaled["boundaries"])
        self.assertEqual(len(downscaled["boundaries"]), len(full["boundaries"]))
        for down, reference in zip(downscaled["boundaries"], full["boundaries"], strict=True):
            np.testing.assert_allclose(down["bbox_xyxy_norm"], reference["bbox_xyxy_norm"], atol=0.02)

    def test_catalog_lists_every_floor_plane_field_at_its_default(self) -> None:
        listed = _catalog_config("floor_plane")
        defaults = FloorPlaneConfig()
        self.assertEqual(set(listed), {item.name for item in fields(FloorPlaneConfig)})
        for item in fields(FloorPlaneConfig):
            self.assertEqual(listed[item.name], getattr(defaults, item.name))

    def test_shared_column_scan_resets_at_roi_gaps_when_requested(self) -> None:
        floor = np.zeros((20, 1), dtype=bool)
        valid = np.ones((20, 1), dtype=bool)
        floor[:8] = True
        valid[8] = False
        floor[9:13] = True
        kept, starts, supports = first_column_hits(floor, valid, 10, 4, reset_at_gaps=True)
        kept_without, starts_without, supports_without = first_column_hits(
            floor, valid, 10, 4, reset_at_gaps=False,
        )
        self.assertEqual(kept.size, 0)
        self.assertEqual(starts.size, 0)
        self.assertEqual(supports.size, 0)
        self.assertEqual(kept_without.tolist(), [0])
        self.assertEqual(int(supports_without[0]), 12)
        self.assertGreater(int(supports_without[0]), 10)

    def test_unusual_sizes_and_edge_dilation_stay_inside_the_image(self) -> None:
        for shape in ((17, 13), (64, 3), (9, 40)):
            rgb = np.full((*shape, 3), 0.45, dtype=np.float32)
            mask, roi, _info = estimate_floor_mask(rgb, FloorPlaneConfig())
            self.assertEqual(mask.shape, shape)
            self.assertEqual(roi.shape, shape)

        config = FloorPlaneConfig(
            horizon_ratio=0.0,
            near_width_ratio=1.0,
            far_width_ratio=1.0,
            min_floor_support=4,
            obstacle_run_length=3,
            boundary_dilate_radius=2,
            morph_radius=0,
        )
        floor = np.zeros((24, 8), dtype=bool)
        floor[14:] = True
        undilated = source_obstacle_hits(
            floor, FloorPlaneConfig(**{**config.__dict__, "boundary_dilate_radius": 0}),
        )
        dilated = source_obstacle_hits(floor, config)
        self.assertEqual(dilated.shape, floor.shape)
        self.assertTrue(undilated.any())
        self.assertGreater(int(dilated.sum()), int(undilated.sum()))
        self.assertTrue(dilated[:, 0].any())
        rows, columns = np.nonzero(undilated)
        self.assertTrue(dilated[rows[0], 0])
        self.assertLess(int(columns.min()), 2)

    def test_still_image_projection_still_writes_its_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            image_path = directory / "still.png"
            Image.new("RGB", (32, 24), (150, 140, 120)).save(image_path)
            result = process_still(image_path, directory / "out", FloorPlaneConfig(horizon_ratio=0.45))
            self.assertEqual(result.config["horizon_ratio"], 0.45)
            self.assertTrue(Path(result.output_files["topdown_rgb"]).is_file())
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = process_still_main(
                    [
                        "--input",
                        str(image_path),
                        "--out-dir",
                        str(directory / "script"),
                        "--horizon",
                        "0.45",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("floor_fraction_roi", stdout.getvalue())
            self.assertTrue((directory / "script" / "topdown_rgb.jpg").is_file())


class FloorContinuityTests(unittest.TestCase):
    def test_centre_band_defaults_and_rejects_an_inverted_pair(self) -> None:
        config = FloorContinuityConfig()
        self.assertEqual((config.center_support_x0, config.center_support_x1), (0.40, 0.60))
        listed = _catalog_config("floor_continuity")
        self.assertEqual(listed["center_support_x0"], 0.40)
        self.assertEqual(listed["center_support_x1"], 0.60)
        with self.assertRaises(FieldError):
            FloorContinuityConfig(center_support_x0=0.70, center_support_x1=0.20)

    def test_batched_scan_ignores_rows_above_the_first_roi_gap(self) -> None:
        floor = np.zeros((30, 2), dtype=bool)
        roi = np.ones((30, 2), dtype=bool)
        floor[20:] = True
        floor[16:20] = False
        roi[15, :] = False
        floor[:12] = True
        columns, _starts, supports = first_column_hits(
            floor[::-1], np.logical_and.accumulate(roi[::-1], axis=0), 8, 4,
        )
        self.assertEqual(columns.tolist(), [0, 1])
        self.assertTrue(np.all(supports >= 8))
        config = FloorContinuityConfig(
            minimum_floor_support_px=8,
            minimum_interruption_run_px=4,
            minimum_boundary_width_ratio=0.01,
        )
        _hits, details = _first_interruptions(floor, roi, np.zeros(floor.shape, dtype=np.float32), config)
        self.assertEqual(sorted(details), [0, 1])
        self.assertGreaterEqual(details[0]["floor_support"], 8)
        self.assertLess(details[0]["y"], 20)


class ExposedSettingTests(unittest.TestCase):
    def test_temporal_floor_settings_validate_and_match_catalog(self) -> None:
        temporal = TemporalFloorContinuityPlugin()
        self.assertEqual(temporal.held_confidence_decay, 0.82)
        self.assertEqual(
            (temporal.overlap_weight, temporal.distance_weight, temporal.confidence_weight),
            (0.55, 0.30, 0.15),
        )
        with self.assertRaises(FieldError):
            TemporalFloorContinuityPlugin(held_confidence_decay=1.2)
        listed = _catalog_config("floor_continuity_temporal")
        self.assertEqual(listed["held_confidence_decay"], 0.82)
        self.assertEqual(listed["overlap_weight"], 0.55)

    def test_cardboard_and_color_target_settings_validate_and_match_catalog(self) -> None:
        cardboard = SimCardboardObstaclePlugin()
        self.assertEqual(
            (cardboard.red_minimum, cardboard.red_maximum, cardboard.green_minimum, cardboard.green_maximum),
            (0.28, 0.86, 0.12, 0.66),
        )
        self.assertEqual(cardboard.blue_maximum, 0.48)
        self.assertEqual((cardboard.red_green_lead, cardboard.green_blue_lead), (0.08, 0.04))
        self.assertEqual(cardboard.chroma_minimum, 0.20)
        self.assertEqual((cardboard.brightness_minimum, cardboard.brightness_maximum), (0.16, 0.72))
        self.assertEqual((cardboard.split_width_ratio, cardboard.split_step_ratio), (0.10, 0.04))
        self.assertEqual((cardboard.split_width_floor_px, cardboard.split_step_floor_px), (5, 3.0))
        with self.assertRaises(FieldError):
            SimCardboardObstaclePlugin(red_minimum=0.90, red_maximum=0.20)
        self.assertEqual(SimCardboardObstaclePlugin(split_step_floor_px=2e6).split_step_floor_px, 2e6)
        cardboard_catalog = _catalog_config("sim_cardboard_obstacle")
        self.assertEqual(cardboard_catalog["working_width"], 640)
        self.assertEqual(cardboard_catalog["red_minimum"], 0.28)
        self.assertEqual(cardboard_catalog["split_step_floor_px"], 3.0)

        color = SimColorTargetsPlugin()
        self.assertEqual((color.evader_red_minimum, color.evader_red_lead), (0.45, 0.12))
        self.assertEqual((color.evader_red_green_ratio, color.evader_red_blue_ratio), (1.25, 1.10))
        self.assertEqual(color.neutral_chroma_maximum, 0.055)
        self.assertEqual((color.neutral_luma_minimum, color.neutral_luma_maximum), (0.18, 0.92))
        self.assertEqual((color.beige_red_blue_minimum, color.beige_row_minimum), (0.035, 0.35))
        with self.assertRaises(FieldError):
            SimColorTargetsPlugin(neutral_luma_minimum=0.95, neutral_luma_maximum=0.20)
        self.assertEqual(SimColorTargetsPlugin(evader_red_green_ratio=2e6).evader_red_green_ratio, 2e6)
        color_catalog = _catalog_config("sim_color_targets")
        self.assertEqual(color_catalog["thumbnail_width"], 320)
        self.assertEqual(color_catalog["evader_red_minimum"], 0.45)
        self.assertEqual(color_catalog["beige_row_minimum"], 0.35)

    def test_tracker_and_fusion_settings_validate_and_match_catalog(self) -> None:
        tracks = MultiObstructionTracksPlugin()
        self.assertEqual((tracks.contour_min_vertices, tracks.contour_max_vertices), (4, 15))
        self.assertEqual((tracks.contour_min_width, tracks.contour_max_width), (0.04, 0.85))
        self.assertEqual((tracks.contour_min_height, tracks.contour_max_height), (0.08, 0.60))
        self.assertEqual((tracks.flow_window, tracks.flow_levels, tracks.feature_seed_cap), (21, 3, 80))
        self.assertEqual(tracks.flow_forward_backward_px, 1.5)
        with self.assertRaises(FieldError):
            MultiObstructionTracksPlugin(contour_min_vertices=20, contour_max_vertices=4)
        MultiObstructionTracksPlugin(adaptive_contours_max_boxes=12)
        tracks_catalog = _catalog_config("multi_obstruction_tracks")
        self.assertEqual(tracks_catalog["flow_window"], 21)
        self.assertEqual(tracks_catalog["contour_min_vertices"], 4)

        fusion = CompositeBoxFusionPlugin()
        self.assertEqual(fusion.corner_max_corners, 600)
        self.assertEqual(fusion.corner_radius_px, 96)
        self.assertEqual((fusion.corner_pair_min_px, fusion.corner_pair_max_px), (24, 360))
        self.assertFalse(fusion.object_separated_geometry)
        self.assertFalse(hasattr(fusion, "_emit_object_separated_geometry"))
        CompositeBoxFusionPlugin(adaptive_contours_max_boxes=9)
        with self.assertRaises(FieldError):
            CompositeBoxFusionPlugin(corner_pair_min_px=400, corner_pair_max_px=24)
        fusion_catalog = _catalog_config("composite_box_fusion")
        self.assertEqual(fusion_catalog["corner_max_corners"], 600)
        self.assertEqual(fusion_catalog["corner_pair_max_px"], 360)
        self.assertFalse(fusion_catalog["object_separated_geometry"])

    def test_avoid_recent_obstruction_forward_throttle_reaches_command(self) -> None:
        avoid = AvoidRecentObstruction()
        self.assertEqual(avoid.forward_throttle, DEFAULT_FORWARD_THROTTLE)
        self.assertEqual(avoid.forward_throttle, 0.60)
        with self.assertRaises(FieldError):
            AvoidRecentObstruction(forward_throttle=1.4)

        evidence = RetainedEvidence(
            record_id="obs_1",
            kind="obstacle",
            label="left box",
            confidence=0.9,
            origin=MemoryOrigin(
                observation_id="obs-frame-1",
                observed_id="box-1",
                coordinate_frame="image",
                observed_at_ms=1000,
                updated_at_ms=1000,
                frame_id="frame-1",
            ),
            location=ViewLocation(frame="image", zone="near_left", bbox_xyxy_norm=(0.1, 0.5, 0.3, 0.8)),
        )
        context = DecisionFrameContext(
            frame_id="frame-1",
            frame_index=1,
            timestamp_ms=1000,
            shared_memory={EVIDENCE_KEY: (evidence,)},
        )
        proposal = AvoidRecentObstruction(forward_throttle=0.35).propose(context)
        self.assertTrue(proposal.available)
        self.assertIsNotNone(proposal.command)
        assert proposal.command is not None
        self.assertEqual(proposal.command.throttle, 0.35)


class ComponentAndPredicateTests(unittest.TestCase):
    def test_equal_area_and_diagonal_components_use_raster_order(self) -> None:
        diagonal = np.zeros((5, 5), dtype=np.uint8)
        diagonal[0, 0] = 1
        diagonal[1, 1] = 1
        x1, y1, x2, y2, area = _largest_component(diagonal)
        self.assertEqual((x1, y1, x2, y2, area), (0, 0, 1, 1, 2))

        tied = np.zeros((6, 6), dtype=np.uint8)
        tied[0:2, 3:5] = 1
        tied[3:5, 0:2] = 1
        x1, y1, x2, y2, area = _largest_component(tied)
        self.assertEqual(area, 4)
        self.assertEqual((x1, y1, x2, y2), (3, 0, 4, 1))

    def test_every_rgb8_value_matches_the_previous_cardboard_predicate(self) -> None:
        plugin = SimCardboardObstaclePlugin()
        channel = np.arange(256, dtype=np.uint8)
        red, green, blue = np.meshgrid(channel, channel, channel, indexing="ij")
        rgb = np.stack((red, green, blue), axis=-1).reshape(-1, 1, 3)
        mask = _cardboard_mask(rgb, plugin).ravel().astype(bool)
        image = rgb.astype(np.float32) / np.float32(255.0)
        r = image[..., 0]
        g = image[..., 1]
        b = image[..., 2]
        brightness = image.mean(axis=-1)
        chroma = image.max(axis=-1) - image.min(axis=-1)
        previous = (
            (r > 0.28) & (r < 0.86) & (g > 0.12) & (g < 0.66) & (b < 0.48)
            & ((r - g) > 0.08) & ((g - b) > 0.04) & (chroma > 0.20)
            & (brightness > 0.16) & (brightness < 0.72)
        ).reshape(-1)
        self.assertEqual(int(mask.sum()), int(previous.sum()))
        self.assertTrue(np.array_equal(mask, previous))


class MotionRankingTests(unittest.TestCase):
    def test_equal_groups_keep_the_earlier_pair_and_inclusive_residuals(self) -> None:
        matches = []
        for index, (sx, sy) in enumerate(
            ((0, 0), (30, 0), (0, 30), (30, 30), (100, 0), (130, 0), (100, 30), (130, 30))
        ):
            if index < 4:
                target = [sx + 10.0, sy]
            else:
                target = [sx, sy + 10.0]
            matches.append(FeatureMatch([float(sx), float(sy)], target, 1.0))
        self.assertEqual(
            find_best_similarity_group(matches, list(range(8)), 1.0),
            [0, 1, 2, 3],
        )
        boundary = list(matches[:4])
        boundary.append(FeatureMatch([60.0, 0.0], [71.0, 0.0], 1.0))
        self.assertEqual(find_best_similarity_group(boundary, list(range(5)), 1.0), [0, 1, 2, 3, 4])
        self.assertEqual(find_best_similarity_group(boundary, list(range(5)), 0.5), [0, 1, 2, 3])

    def test_keypoint_ranking_keeps_a_point_at_exactly_the_minimum_distance(self) -> None:
        gray = np.zeros((48, 64), dtype=np.float32)
        gray[16:22, 16:22] = 255
        gray[16:22, 27:33] = 255
        selected = detect_keypoints(gray, [0, 0, 63, 47], max_features=8, min_distance=5, patch_radius=1)
        self.assertGreaterEqual(len(selected), 2)
        for left_index, left in enumerate(selected):
            for right in selected[left_index + 1:]:
                distance = (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2
                self.assertGreaterEqual(distance, 25)


class FusionTests(unittest.TestCase):
    def test_prepared_cue_image_reuses_resized_and_gray_rasters_per_instance(self) -> None:
        rgb = np.zeros((100, 200, 3), dtype=np.uint8)
        prepared = PreparedCueImage(rgb, 100)
        first_working = prepared.working_rgb()
        self.assertIs(prepared.working_rgb(), first_working)
        self.assertEqual(first_working.shape, (50, 100, 3))
        first_gray = prepared.gray_variants()
        self.assertIs(prepared.gray_variants(), first_gray)
        self.assertEqual(set(first_gray), {"gray", "lab_l", "hsv_v", "clahe"})

    def test_equal_scores_clipped_edges_and_the_corner_cap(self) -> None:
        candidates = [
            ((0.0, 0.0, 0.2, 0.2), 0.5, "corner_junctions", 0, "corner_support", "first", {}),
            ((0.6, 0.6, 0.8, 0.8), 0.5, "corner_junctions", 1, "corner_support", "second", {}),
            ((0.0, 0.0, 0.2, 0.2), 0.5, "corner_junctions", 2, "corner_support", "overlap", {}),
        ]
        kept = _keep_scored_boxes(candidates, threshold=0.5, max_boxes=10)
        self.assertEqual([item.thing_id for item in kept], [
            "corner_junctions_candidate_000",
            "corner_junctions_candidate_001",
        ])
        clipped = _normalized_xywh(-20, -10, 40, 30, 100, 80)
        self.assertEqual(clipped[0], 0.0)
        self.assertEqual(clipped[1], 0.0)
        self.assertLessEqual(clipped[2], 1.0)
        self.assertLessEqual(clipped[3], 1.0)

        image = np.zeros((120, 160, 3), dtype=np.uint8)
        image[::6, :] = 255
        image[:, ::6] = 255
        limited = _corner_junctions(
            image, working_width=160, max_boxes=40, max_corners=4,
            corner_radius_px=40, pair_min_px=1, pair_max_px=400,
        )
        wider = _corner_junctions(
            image, working_width=160, max_boxes=40, max_corners=80,
            corner_radius_px=40, pair_min_px=1, pair_max_px=400,
        )
        self.assertLessEqual(_corner_points(limited), 4)
        self.assertGreater(len(wider), len(limited))
        for thing in wider:
            bbox = thing.location.bbox_xyxy_norm
            self.assertTrue(all(0.0 <= value <= 1.0 for value in bbox))

    def test_border_windows_match_direct_means(self) -> None:
        gray = np.arange(80 * 90, dtype=np.float64).reshape(80, 90) % 37
        integral = cv2.integral(gray.astype(np.float64), sdepth=cv2.CV_64F)
        windows = (
            (0, 0, 24, 24),
            (66, 56, 90, 80),
            (0, 56, 24, 80),
        )
        for x0, y0, x1, y1 in windows:
            got = _integral_mean(
                integral, np.array([x0]), np.array([y0]), np.array([x1]), np.array([y1]),
            )
            self.assertTrue(np.isclose(got[0], gray[y0:y1, x0:x1].mean()))

    def test_two_point_contour_is_null_and_an_ordinary_contour_remains(self) -> None:
        two_points = [[0.1, 0.2], [0.4, 0.5]]
        ordinary_points = [[0.1, 0.2], [0.4, 0.5], [0.2, 0.8]]
        bbox = (0.1, 0.2, 0.4, 0.6)
        degenerate = ViewLocation(
            frame="image",
            zone="center",
            bbox_xyxy_norm=bbox,
            polygon_xy_norm=_polygon_xy_norm(two_points),
        )
        ordinary = ViewLocation(
            frame="image",
            zone="center",
            bbox_xyxy_norm=bbox,
            polygon_xy_norm=_polygon_xy_norm(ordinary_points),
        )
        self.assertIsNone(degenerate.polygon_xy_norm)
        self.assertEqual(degenerate.bbox_xyxy_norm, bbox)
        self.assertEqual(ordinary.polygon_xy_norm, ((0.1, 0.2), (0.4, 0.5), (0.2, 0.8)))
        rgb = np.zeros((48, 64, 3), dtype=np.uint8)
        rgb[:24] = (20, 20, 20)
        rgb[24:] = (180, 40, 20)
        plugin = CompositeBoxFusionPlugin(jev_enabled=False, cache_responses=False, classical_working_width=160)
        things, _diagnostic = plugin._classical_regions(rgb)
        self.assertTrue(things)
        for thing in things:
            polygon = thing.location.polygon_xy_norm
            self.assertTrue(polygon is None or len(polygon) >= 3)
            self.assertIsNotNone(thing.location.bbox_xyxy_norm)
            self.assertIn("contour_xy_norm", thing.properties)

    def test_floor_candidate_handles_null_and_short_polygons(self) -> None:
        plugin = CompositeBoxFusionPlugin(
            enabled_substrategies=("floor_context",),
            jev_enabled=False,
            cache_responses=False,
        )
        mask = np.zeros((4, 4), dtype=bool)
        analysis = FloorContinuityAnalysis(
            source_width=64,
            source_height=48,
            working_width=64,
            working_height=48,
            floor_mask=mask,
            boundary_mask=mask,
            floor=None,
            boundaries=(
                {"bbox_xyxy_norm": (0.1, 0.5, 0.4, 0.7), "polygon_xy_norm": None, "confidence": 0.8},
                {"bbox_xyxy_norm": (0.5, 0.5, 0.8, 0.7), "polygon_xy_norm": [(0.5, 0.5), (0.8, 0.7)], "confidence": 0.7},
            ),
            measurements={"floor_fraction_roi": 0.5},
        )
        inputs = PerceptionPluginInputs(
            frame_id="frame-1",
            captured_at_ms=1000,
            feeds={"frame": CameraFrame("front_camera", 1000, np.zeros((48, 64, 3), dtype=np.uint8))},
            shared_memory={},
        )
        with mock.patch(
            "implementations.decision_cycle.perception.plugins.composite_box_fusion.plugin.analyze_floor_continuity",
            return_value=analysis,
        ):
            batch = plugin.perceive(inputs)
        floor_proposals = [
            record for record in batch.measurements["composite"]["raw_source_proposals"]
            if record["source"] == "floor_continuity"
        ]
        self.assertEqual(batch.measurements["composite"]["floor"], {"floor_fraction_roi": 0.5})
        self.assertEqual(len(floor_proposals), 2)
        for record in floor_proposals:
            self.assertIsNone(record["geometry"]["polygon_xy_norm"])

    def test_consolidated_presets_report_the_base_plugin_id(self) -> None:
        capture = preset_activation("perception", "capture_floor")
        self.assertEqual(capture.plugins, ("floor_continuity",))
        self.assertEqual(
            capture.plugin_configs["floor_continuity"]["minimum_boundary_width_ratio"], 0.03,
        )
        self.assertEqual(
            capture.plugin_configs["floor_continuity"]["minimum_boundary_confidence"], 0.7,
        )
        floor = FloorContinuityPlugin(**capture.plugin_configs["floor_continuity"])
        self.assertEqual(floor.plugin_id, "floor_continuity")

        separated = preset_activation("perception", "object_separated_fusion")
        self.assertEqual(separated.plugins, ("composite_box_fusion",))
        config = separated.plugin_configs["composite_box_fusion"]
        self.assertTrue(config["object_separated_geometry"])
        self.assertEqual(config["corner_max_corners"], 600)
        plugin = CompositeBoxFusionPlugin(**config)
        self.assertEqual(plugin.plugin_id, "composite_box_fusion")
        self.assertTrue(plugin.object_separated_geometry)
        offline = CompositeBoxFusionPlugin(**{**config, "jev_enabled": False, "cache_responses": False})
        self.assertFalse(offline.jev_enabled)
        self.assertFalse(offline.cache_responses)
        specs = {item["spec"] for item in step_plugins("perception").values()}
        self.assertFalse(any("floor_continuity_capture" in spec for spec in specs))
        self.assertFalse(any("object_separated" in spec for spec in specs))


def _corner_points(things) -> int:
    points = set()
    for thing in things:
        anchor = thing.properties.get("anchor_xy_px")
        if anchor is not None:
            points.add(tuple(anchor))
        for point in thing.properties.get("pair_xy_px", ()):
            points.add(tuple(point))
    return len(points)


if __name__ == "__main__":
    unittest.main(verbosity=2)
