from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = (
    ROOT
    / "docs"
    / "milestones"
    / "007-cli-operator-usability"
    / "tools"
    / "capability-disposition"
)
MODULE_PATH = TOOL / "capability_disposition.py"
SPEC = importlib.util.spec_from_file_location("m007_capability_disposition", MODULE_PATH)
assert SPEC and SPEC.loader
cd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cd)


class CapabilityDispositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.context = cd._build_context(ROOT)
        cls.sealed = cls.context["sealed"]
        cls.artifact = cls.context["artifact"]
        cls.grouping = cls.context["grouping"]
        cls.candidates = cd._derive_candidates(cls.sealed, cls.artifact)
        cls.record = cd.assemble_record(
            sealed=cls.sealed,
            artifact=cls.artifact,
            grouping=cls.grouping,
            source_analysis_sha256=cls.context["source_analysis_sha256"],
            grouping_sha256=cls.context["grouping_sha256"],
        )

    def test_committed_evidence_passes(self) -> None:
        result = cd.validate_evidence(ROOT)
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["candidate_member_count"], 93)
        self.assertEqual(result["group_count"], 10)

    def test_source_universe_keeps_absent_files(self) -> None:
        member = self.candidates["implementations/perception/features/feature_sequence.py"]
        self.assertTrue(member["unreached_statements"])
        self.assertTrue(member["unreached_arcs"])
        self.assertNotIn(
            "implementations/perception/features/feature_sequence.py",
            self.sealed["files"],
        )
        self.assertIn("implementations/perception/features/feature_sequence.py", self.sealed["source_paths"])

    def test_role_selector_excludes_support_only_app_line(self) -> None:
        member = self.candidates["cli/automa_cli/app.py"]
        self.assertIn(1662, member["unreached_statements"])
        self.assertTrue(
            any(
                context["logical_context_id"].startswith("m007/support/")
                and 1662 in context["executed_lines"]
                for context in self.sealed["files"]["cli/automa_cli/app.py"]
            )
        )
        self.assertNotIn(
            "m007/support/cleanup/m007-acceptance/cmd-01",
            self.sealed["admitted_contexts"],
        )

    def test_only_three_files_are_fully_journey_reached(self) -> None:
        self.assertEqual(
            set(self.sealed["source_paths"]) - set(self.candidates),
            {
                "autonomy/decision/plugin.py",
                "implementations/perception/observation/plugin.py",
                "implementations/vehicle/chase_sim/defaults.py",
            },
        )

    def test_grouping_has_exact_candidate_parity(self) -> None:
        assigned = [
            path
            for group in self.grouping["groups"]
            for path in group["member_paths"]
        ]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(set(assigned), set(self.candidates))
        self.assertEqual(
            [group["id"] for group in self.grouping["groups"]],
            sorted(group["id"] for group in self.grouping["groups"]),
        )

    def test_missing_member_fails_closed(self) -> None:
        mutated = copy.deepcopy(self.grouping)
        mutated["groups"][0]["member_paths"].pop()
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_grouping(
                mutated,
                repo_root=ROOT,
                source_paths=self.sealed["source_paths"],
                candidate_paths=set(self.candidates),
                authority=self.context["authority"],
            )

    def test_duplicate_member_fails_closed(self) -> None:
        mutated = copy.deepcopy(self.grouping)
        path = mutated["groups"][0]["member_paths"][0]
        mutated["groups"][1]["member_paths"].append(path)
        mutated["groups"][1]["member_paths"].sort()
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_grouping(
                mutated,
                repo_root=ROOT,
                source_paths=self.sealed["source_paths"],
                candidate_paths=set(self.candidates),
                authority=self.context["authority"],
            )

    def test_unknown_member_fails_closed(self) -> None:
        mutated = copy.deepcopy(self.grouping)
        mutated["groups"][0]["member_paths"][0] = "autonomy/not-sealed.py"
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_grouping(
                mutated,
                repo_root=ROOT,
                source_paths=self.sealed["source_paths"],
                candidate_paths=set(self.candidates),
                authority=self.context["authority"],
            )

    def test_reconcile_dimensions_are_required(self) -> None:
        mutated = copy.deepcopy(self.grouping)
        del mutated["groups"][0]["reconcile"]["platform_paths"]
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_grouping(
                mutated,
                repo_root=ROOT,
                source_paths=self.sealed["source_paths"],
                candidate_paths=set(self.candidates),
                authority=self.context["authority"],
            )

    def test_not_applicable_requires_a_reason(self) -> None:
        mutated = copy.deepcopy(self.grouping)
        mutated["groups"][0]["reconcile"]["dynamic_paths"]["reason"] = ""
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_grouping(
                mutated,
                repo_root=ROOT,
                source_paths=self.sealed["source_paths"],
                candidate_paths=set(self.candidates),
                authority=self.context["authority"],
            )

    def test_present_requires_a_reference(self) -> None:
        mutated = copy.deepcopy(self.grouping)
        mutated["groups"][0]["reconcile"]["non_cli_entrypoints"] = {
            "status": "present",
            "refs": [],
            "reason": "",
        }
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_grouping(
                mutated,
                repo_root=ROOT,
                source_paths=self.sealed["source_paths"],
                candidate_paths=set(self.candidates),
                authority=self.context["authority"],
            )

    def test_free_text_reason_reference_fails_closed(self) -> None:
        mutated = copy.deepcopy(self.grouping)
        mutated["groups"][0]["reason"]["reference"] = {
            "kind": "reconciliation_ref",
            "dimension": "non_cli_entrypoints",
            "ref": "not-qualified",
        }
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_grouping(
                mutated,
                repo_root=ROOT,
                source_paths=self.sealed["source_paths"],
                candidate_paths=set(self.candidates),
                authority=self.context["authority"],
            )

    def test_metric_and_reachability_language_is_rejected(self) -> None:
        bad_values = (
            "20%",
            "20 percent",
            "one in four",
            "one-in-four",
            "one out of four",
            "one-out-of-four",
            "twenty-one lines",
            "coverage",
            "not-covered",
            "never executed",
        )
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(cd.CapabilityDispositionError):
                    cd.validate_non_metric_text(value, "reason.detail")

    def test_metric_reason_is_rejected_in_a_group(self) -> None:
        for index, disposition in ((3, "expose"), (0, "retain"), (0, "remove")):
            with self.subTest(disposition=disposition):
                mutated = copy.deepcopy(self.grouping)
                group = mutated["groups"][index]
                group["disposition"] = disposition
                if disposition == "remove":
                    path = group["member_paths"][0]
                    group["reason"] = {
                        "code": "separate_removal_review",
                        "reference": {
                            "kind": "source_member",
                            "path": path,
                            "source_sha256": self.sealed["source_paths"][path],
                        },
                        "detail": "The owner has 20 percent of the surface.",
                    }
                else:
                    group["reason"]["detail"] = "The owner has 20 percent of the surface."
                with self.assertRaises(cd.CapabilityDispositionError):
                    cd.validate_grouping(
                        mutated,
                        repo_root=ROOT,
                        source_paths=self.sealed["source_paths"],
                        candidate_paths=set(self.candidates),
                        authority=self.context["authority"],
                    )

    def test_source_analysis_missing_arc_fails_closed(self) -> None:
        mutated = copy.deepcopy(self.artifact)
        row = next(
            item
            for item in mutated["files"]
            if item["path"] == "cli/automa_cli/app.py"
        )
        row["possible_arcs"].pop()
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_source_analysis(mutated, ROOT, self.sealed["source_paths"])

    def test_source_analysis_missing_file_fails_closed(self) -> None:
        mutated = copy.deepcopy(self.artifact)
        mutated["files"].pop()
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_source_analysis(mutated, ROOT, self.sealed["source_paths"])

    def test_record_region_omission_fails_closed(self) -> None:
        mutated = copy.deepcopy(self.record)
        member = next(
            member
            for group in mutated["groups"]
            for member in group["members"]
            if member["path"] == "cli/automa_cli/app.py"
        )
        member["unreached_arcs"].pop()
        mutated["integrity"]["record_sha256"] = cd.record_digest(mutated)
        with self.assertRaises(cd.CapabilityDispositionError):
            cd.validate_record(mutated, expected=self.record)

    def test_derived_html_omission_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.html"
            path.write_text(cd.render_html(self.record), encoding="utf-8")
            cd.validate_html(path, self.record)
            html = path.read_text(encoding="utf-8")
            marker = '<tr data-group-id="autonomy-decision-runtime"'
            start = html.index(marker)
            end = html.index("</tr>", start) + len("</tr>")
            path.write_text(html[:start] + html[end:], encoding="utf-8")
            with self.assertRaises(cd.CapabilityDispositionError):
                cd.validate_html(path, self.record)

    def test_dashboard_matches_record_projection(self) -> None:
        projection = cd._dashboard_projection(self.record, self.sealed)
        self.assertEqual(projection["membership"]["source_members"], 96)
        self.assertEqual(projection["membership"]["journey_contexts"], 22)
        self.assertEqual(projection["membership"]["candidate_members"], 93)
        self.assertEqual(projection["source_status"], {
            "fully_reached": 3,
            "candidate_partial": 60,
            "candidate_absent": 33,
        })
        self.assertEqual(projection["dispositions"], {
            "expose": 25,
            "retain": 68,
            "remove": 0,
        })
        self.assertEqual(projection["journey_overview"]["surface"], {
            "leaf_total": 49,
            "leaf_kind_counts": {"action": 32, "alias": 7, "meta": 10},
            "measured_leaf_count": 11,
            "unmeasured_leaf_count": 38,
            "measured_leaf_ids": [
                "help",
                "vehicles.automation.help",
                "vehicles.automation.run",
                "vehicles.automation.stop",
                "vehicles.help",
                "vehicles.memory.check",
                "vehicles.perception.apply",
                "vehicles.perception.run",
                "vehicles.status",
                "vehicles.update.memory",
                "vehicles.update.perception",
            ],
            "measured_leaf_kind_counts": {"action": 8, "meta": 3},
        })
        self.assertEqual(
            [journey["id"] for journey in projection["journey_overview"]["journeys"]],
            [
                "primary",
                "continuity.offline_perception",
                "continuity.live_config_swap",
                "continuity.memory_lifecycle",
            ],
        )
        self.assertEqual(projection["journey_overview"]["sequences"]["coverage"], {
            "measured": 2,
            "not_applicable": 1,
            "unmeasured": 7,
        })
        cd.validate_dashboard_html(
            ROOT / cd.DASHBOARD_REL,
            self.record,
            self.sealed,
            self.context["authority"],
        )

    def test_dashboard_group_omission_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.html"
            path.write_text(
                cd.render_dashboard_html(self.record, self.sealed, self.context["authority"]),
                encoding="utf-8",
            )
            cd.validate_dashboard_html(
                path, self.record, self.sealed, self.context["authority"]
            )
            source = path.read_text(encoding="utf-8")
            marker = '<button type="button" class="group-row'
            start = source.index(marker)
            end = source.index(">", start)
            button = source[start:end]
            group_id = self.record["groups"][0]["id"]
            button = button.replace(f' data-group-id="{group_id}"', "")
            path.write_text(source[:start] + button + source[end:], encoding="utf-8")
            with self.assertRaises(cd.CapabilityDispositionError):
                cd.validate_dashboard_html(path, self.record, self.sealed)

    def test_canonical_grouping_is_committed(self) -> None:
        grouping_path = ROOT / cd.GROUPING_REL
        raw = grouping_path.read_bytes()
        self.assertEqual(cd.canonical_file_bytes(self.grouping), raw)

    def test_record_contains_exact_frozen_input_manifest(self) -> None:
        self.assertEqual(
            self.record["inputs"]["m007_08"]["input_manifest"],
            cd.FROZEN_M007_08_MANIFEST,
        )
        self.assertEqual(
            self.record["inputs"]["journey_coverage"]["role_selector"]["admitted_context_count"],
            22,
        )


if __name__ == "__main__":
    unittest.main()
