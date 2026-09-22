from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from autonomy.perception import PerceivedThing, ViewLocation
from lab.plugins.perception.composite_box_fusion.src.plugin import (
    CompositeBoxFusionPlugin,
)
from lab.plugins.perception.composite_box_fusion.src.geometry import (
    GeometryProposal,
    cluster_proposals,
    compare_selectors,
    make_hypotheses,
    support_confidence,
    source_balanced_budget,
    weighted_median_bbox,
)
from lab.plugins.perception.multi_obstruction_tracks.src.plugin import (
    MultiObstructionTracksPlugin,
)


class CompositeBoxFusionTests(unittest.TestCase):
    def test_shared_candidate_keeps_substrategy_measurements_and_contract(self) -> None:
        rgb = np.zeros((120, 160, 3), dtype=np.uint8)
        rgb[:] = (115, 105, 92)
        rgb[74:, :] = (145, 122, 98)
        rgb[32:82, 18:62] = (198, 172, 136)
        rgb[27:84, 91:138] = (218, 218, 210)

        plugin = CompositeBoxFusionPlugin(
            issue_working_width=320,
            classical_working_width=160,
            max_tracks=4,
        )
        candidates, gray, summary = plugin._detect_candidates(rgb)

        self.assertEqual(gray.shape, rgb.shape[:2])
        self.assertEqual(
            summary["enabled_substrategies"],
            [
                "edge_contours",
                "partial_faces",
                "photometric_regions",
                "floor_context",
                "line_junction_support",
            ],
        )
        self.assertIn("current_edge_contours", summary["raw_counts_by_source"])
        self.assertIn("classical_regions", summary["raw_counts_by_source"])
        self.assertIn("floor_continuity", summary["raw_counts_by_source"])
        self.assertIn("corner_junctions", summary["support_counts_by_source"])
        self.assertTrue(all(thing.kind == "region_proposal" for thing in candidates))
        raw_proposals = summary["raw_source_proposals"]
        self.assertTrue(raw_proposals)
        self.assertTrue(
            all(
                isinstance(proposal["geometry"], dict)
                and isinstance(proposal["properties"], dict)
                and isinstance(proposal["rejection_reasons"], list)
                for proposal in raw_proposals
            )
        )
        self.assertTrue(any(proposal["rejection_reasons"] for proposal in raw_proposals))
        self.assertTrue(summary["support_proposals"])
        self.assertEqual(plugin.contract.state_mode, "windowed")
        self.assertTrue(any("multiple image-space obstacle records" in item for item in plugin.contract.emits))

    def test_line_and_junction_cues_are_support_only(self) -> None:
        rgb = np.full((96, 128, 3), 120, dtype=np.uint8)
        rgb[28:74, 24:70] = (210, 205, 198)
        plugin = CompositeBoxFusionPlugin(
            issue_working_width=320,
            classical_working_width=160,
        )
        candidates, _gray, summary = plugin._detect_candidates(rgb)

        self.assertTrue(summary["support_counts_by_source"])
        self.assertFalse(any(thing.properties.get("source") in {"corner_junctions", "hough_fragments"} for thing in candidates))

    def test_photometric_sources_share_one_family(self) -> None:
        plugin = CompositeBoxFusionPlugin(enabled_substrategies=("photometric_regions",))
        thing = PerceivedThing(
            thing_id="cue",
            kind="region_proposal",
            label="cue",
            location=ViewLocation(
                frame="image",
                zone="mid_center",
                bbox_xyxy_norm=(0.2, 0.2, 0.4, 0.5),
            ),
            confidence=0.5,
        )

        for source in ("classical_regions", "photometric_windows"):
            decorated = plugin._decorate(source, 0, thing)
            self.assertEqual(decorated.properties["strategy_family"], "photometric_family")

    def test_parent_current_edge_path_receives_raw_frame_once(self) -> None:
        rgb = np.random.default_rng(13).integers(0, 256, (72, 96, 3), dtype=np.uint8)
        plugin = CompositeBoxFusionPlugin(
            contrast_normalization="clahe",
            enabled_substrategies=("edge_contours",),
            issue_working_width=320,
        )
        parent_inputs: list[np.ndarray] = []

        def fake_parent_detect(_parent: MultiObstructionTracksPlugin, image: np.ndarray):
            parent_inputs.append(np.array(image, copy=True))
            return [], np.zeros(image.shape[:2], dtype=np.uint8), {}

        with patch.object(
            MultiObstructionTracksPlugin,
            "_detect_candidates",
            new=fake_parent_detect,
        ):
            plugin._detect_candidates(rgb)

        self.assertEqual(len(parent_inputs), 1)
        self.assertTrue(np.array_equal(parent_inputs[0], rgb))
        self.assertFalse(np.array_equal(plugin._normalized_luminance_rgb(rgb), rgb))

    def test_geometry_budget_split_hypotheses_and_selectors_are_recorded(self) -> None:
        proposals = [
            GeometryProposal(
                "left",
                "canny_morph",
                "edge_family",
                (0.10, 0.30, 0.30, 0.70),
                0.90,
                {},
            ),
            GeometryProposal(
                "bridge",
                "classical_regions",
                "photometric_family",
                (0.20, 0.30, 0.60, 0.70),
                0.80,
                {},
            ),
            GeometryProposal(
                "right",
                "partial_contour",
                "edge_family",
                (0.50, 0.30, 0.70, 0.70),
                0.85,
                {},
            ),
            GeometryProposal(
                "floor",
                "floor_continuity",
                "floor_family",
                (0.00, 0.72, 1.00, 1.00),
                0.10,
                {},
            ),
        ]
        budgeted, budget_rejections = source_balanced_budget(proposals, limit=3)
        self.assertEqual(len(budgeted), 3)
        self.assertEqual(budget_rejections, {"floor": ["raw_proposal_budget_exceeded"]})

        base, split, rejected, split_events = cluster_proposals(
            budgeted + [proposals[-1]],
            iou_threshold=0.12,
            center_distance_threshold=0.07,
            max_clusters=12,
            split_spatial_modes=True,
            split_center_gap=0.14,
        )
        self.assertEqual(len(base), 1)
        self.assertEqual(len(split), 2)
        self.assertTrue(split_events)
        self.assertEqual(rejected["floor"], ["context_only_floor_evidence"])

        hypotheses = {
            cluster_id: make_hypotheses(
                cluster_id,
                members,
                max_hypotheses=10,
                robust_low_quantile=0.10,
                robust_high_quantile=0.90,
                robust_padding=0.014,
            )
            for cluster_id, members in split.items()
        }
        for cluster_hypotheses in hypotheses.values():
            self.assertTrue(
                {item.kind for item in cluster_hypotheses}
                >= {"raw", "covering_union", "robust_extent", "weighted_median", "intersection", "none"}
            )
        selectors = compare_selectors(hypotheses)
        self.assertEqual(
            set(selectors),
            {
                "highest_raw_confidence",
                "covering_union",
                "robust_extent",
                "context_aware_heuristic",
            },
        )
        self.assertTrue(all(set(choice) == set(hypotheses) for choice in selectors.values()))

    def test_plugin_persists_geometry_and_fixed_jev_evidence(self) -> None:
        rgb = np.zeros((120, 160, 3), dtype=np.uint8)
        rgb[:] = (115, 105, 92)
        rgb[74:, :] = (145, 122, 98)
        rgb[32:82, 18:62] = (198, 172, 136)
        rgb[27:84, 91:138] = (218, 218, 210)

        plugin = CompositeBoxFusionPlugin(
            issue_working_width=320,
            classical_working_width=160,
            jev_enabled=False,
        )
        candidates, _gray, summary = plugin._detect_candidates(rgb)
        geometry = summary["geometry"]

        self.assertEqual(geometry["raw_proposal_budget"], 120)
        self.assertIn("base_clusters", geometry)
        self.assertIn("split_clusters", geometry)
        self.assertIn("hypotheses", geometry)
        self.assertEqual(
            set(geometry["selector_choices"]),
            {
                "highest_raw_confidence",
                "covering_union",
                "robust_extent",
                "context_aware_heuristic",
            },
        )
        self.assertEqual(geometry["jev"]["status"], "disabled")
        self.assertIn("fixed_jev_prompt", geometry)
        self.assertTrue(all(thing.kind == "region_proposal" for thing in candidates))

    def test_geometry_rejection_does_not_fall_back_to_pre_geometry_candidates(self) -> None:
        plugin = CompositeBoxFusionPlugin(jev_enabled=False)
        fallback = PerceivedThing(
            thing_id="pre_geometry_candidate",
            kind="region_proposal",
            label="fallback",
            location=ViewLocation(
                frame="image",
                zone="mid_center",
                bbox_xyxy_norm=(0.2, 0.2, 0.4, 0.5),
            ),
            confidence=0.8,
        )

        result = plugin._enumerate_geometry([], [fallback], [])

        self.assertEqual(result["tracking_candidates"], [])
        self.assertEqual(result["summary"]["tracking_candidate_ids"], [])

    def test_context_selector_can_choose_none_for_unsupported_cluster(self) -> None:
        proposal = GeometryProposal(
            "weak",
            "canny_morph",
            "edge_family",
            (0.2, 0.2, 0.3, 0.35),
            0.2,
            {},
        )
        hypotheses = make_hypotheses(
            "cluster",
            [proposal],
            max_hypotheses=10,
            robust_low_quantile=0.10,
            robust_high_quantile=0.90,
            robust_padding=0.014,
        )

        choice = compare_selectors({"cluster": hypotheses})["context_aware_heuristic"]["cluster"]

        self.assertEqual(choice.kind, "none")
        self.assertTrue(choice.valid)
        self.assertGreater(choice.none_score, 0.0)

    def test_family_support_does_not_count_correlated_edge_sources_independently(self) -> None:
        correlated = [
            GeometryProposal(
                "canny",
                "canny_morph",
                "edge_family",
                (0.2, 0.2, 0.4, 0.5),
                0.9,
                {},
            ),
            GeometryProposal(
                "partial",
                "partial_contour",
                "edge_family",
                (0.21, 0.2, 0.41, 0.5),
                0.8,
                {},
            ),
        ]
        independent = correlated + [
            GeometryProposal(
                "photo",
                "classical_regions",
                "photometric_family",
                (0.2, 0.2, 0.4, 0.5),
                0.8,
                {},
            )
        ]

        correlated_hypotheses = make_hypotheses(
            "correlated",
            correlated,
            max_hypotheses=10,
            robust_low_quantile=0.10,
            robust_high_quantile=0.90,
            robust_padding=0.014,
        )
        independent_hypotheses = make_hypotheses(
            "independent",
            independent,
            max_hypotheses=10,
            robust_low_quantile=0.10,
            robust_high_quantile=0.90,
            robust_padding=0.014,
        )
        correlated_union = next(item for item in correlated_hypotheses if item.kind == "covering_union")
        independent_union = next(item for item in independent_hypotheses if item.kind == "covering_union")

        self.assertEqual(correlated_union.family_names, ("edge_family",))
        self.assertEqual(len(correlated_union.family_support), 1)
        self.assertEqual(
            independent_union.family_names,
            ("edge_family", "photometric_family"),
        )
        self.assertGreater(
            support_confidence(independent),
            support_confidence(correlated),
        )
        self.assertEqual(weighted_median_bbox(correlated), (0.2, 0.2, 0.4, 0.5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
