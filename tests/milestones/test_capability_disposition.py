from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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

    @staticmethod
    def _stage_sealed_report_root(root: Path) -> Path:
        for name in ("autonomy", "implementations", "cli"):
            (root / name).symlink_to(ROOT / name, target_is_directory=True)
        (root / ".coveragerc").symlink_to(ROOT / ".coveragerc")
        for relative in (cd.REPORT_REL, cd.MANIFEST_REL):
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / relative).read_bytes())
        return root / cd.MANIFEST_REL

    def test_sealed_manifest_drift_fails_closed(self) -> None:
        for mutation in ("changed", "missing"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    manifest_path = self._stage_sealed_report_root(root)
                    if mutation == "changed":
                        manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
                    else:
                        manifest_path.unlink()
                    with self.assertRaises(cd.CapabilityDispositionError):
                        cd.load_sealed_report(root)

    def test_historical_validation_ignores_current_product_source_drift(self) -> None:
        path = "cli/automa_cli/app.py"
        frozen_sha256 = self.sealed["source_paths"][path]
        self.assertNotEqual(cd.sha256_file(ROOT / path), frozen_sha256)

        current = cd._build_context(ROOT)
        frozen_source = current["sealed"]["source_reader"].read(path, frozen_sha256)
        expected_row = next(row for row in self.artifact["files"] if row["path"] == path)
        self.assertNotEqual((ROOT / path).read_bytes(), frozen_source)
        self.assertEqual(
            cd._possible_regions(
                path,
                ROOT,
                source_paths=self.sealed["source_paths"],
                source_reader=current["sealed"]["source_reader"],
            ),
            (expected_row["possible_statements"], expected_row["possible_arcs"]),
        )

    def test_frozen_config_hash_mismatch_fails_before_git_resolution(self) -> None:
        report = cd.load_canonical_json(ROOT / cd.REPORT_REL)
        mutated = copy.deepcopy(report)
        row = next(
            row
            for row in mutated["subject"]["source_identity"]["relevant"]["files"]
            if row["path"] == ".coveragerc"
        )
        row["sha256"] = "0" * 64
        reader = cd.FrozenGitSource(
            ROOT,
            blob_reader=mock.Mock(side_effect=AssertionError("Git must not be called")),
        )
        with self.assertRaisesRegex(
            cd.CapabilityDispositionError, "sealed \.coveragerc identity"
        ):
            cd._source_files_from_report(mutated, ROOT, source_reader=reader)

    def test_frozen_source_rejects_unsafe_and_unadmitted_paths(self) -> None:
        reader = cd.FrozenGitSource(
            ROOT,
            blob_reader=mock.Mock(side_effect=AssertionError("resolver must not be called")),
        )
        reader.bind({"cli/automa_cli/app.py": "0" * 64})
        for path in (
            "/tmp/app.py",
            "../cli/automa_cli/app.py",
            "cli\\automa_cli\\app.py",
            "cli/automa_cli/app.py\x00",
            "cli/automa_cli/not-admitted.py",
        ):
            with self.subTest(path=path):
                with self.assertRaises(cd.CapabilityDispositionError):
                    reader.read(path)

    def test_frozen_source_hash_mismatch_fails_before_parsing(self) -> None:
        path = "cli/automa_cli/app.py"
        reader = cd.FrozenGitSource(ROOT, blob_reader=lambda _path: b"not the frozen source")
        reader.bind({path: cd.sha256_bytes(b"expected frozen source")})
        with self.assertRaisesRegex(
            cd.CapabilityDispositionError, "frozen source hash mismatch"
        ):
            reader.read(path)

    def test_frozen_git_resolution_failure_classes_fail_closed(self) -> None:
        path = "cli/automa_cli/app.py"
        expected_sha256 = cd.sha256_bytes(b"frozen source")

        def result(returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> mock.Mock:
            return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)

        cases = (
            (
                "missing commit",
                [result(returncode=1, stderr=b"missing commit")],
                "frozen source commit is missing",
            ),
            (
                "missing blob",
                [result(), result(returncode=1, stderr=b"missing path")],
                "frozen source blob is missing",
            ),
            (
                "non-blob path",
                [result(), result(stdout=b"tree\n")],
                "frozen source path is not a blob",
            ),
            (
                "unreadable blob",
                [result(), result(stdout=b"blob\n"), result(returncode=1, stderr=b"corrupt")],
                "frozen source blob is unreadable",
            ),
            (
                "partial blob output",
                [result(), result(stdout=b"blob\n"), result(stdout=b"frozen", stderr=b"warning")],
                "frozen source blob is unreadable",
            ),
        )
        for name, responses, message in cases:
            with self.subTest(failure=name):
                runner = mock.Mock(side_effect=responses)
                reader = cd.FrozenGitSource(ROOT, git_runner=runner)
                reader.bind({path: expected_sha256})
                with self.assertRaisesRegex(cd.CapabilityDispositionError, message):
                    reader.read(path)
                commands = [call.args[0] for call in runner.call_args_list]
                self.assertEqual(commands[0][0:4], ["git", "--no-replace-objects", "cat-file", "-e"])
                if len(commands) > 1:
                    self.assertIn(f"{cd.FROZEN_SOURCE_COMMIT}:{path}", commands[1])

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

    def test_m007_owner_reference_must_match_cited_artifact(self) -> None:
        authority = self.context["authority"]
        leaf_path = next(
            entry["path"]
            for entry in cd.FROZEN_M007_08_MANIFEST
            if entry["id"] == "leaf_inventory"
        )
        leaf_digest = authority["digests"][leaf_path]
        owner = "cli-perception-offline"
        cross_artifact_ref = f"m007_08_owner:{owner}@{leaf_digest}"
        self.assertIn(owner, authority["owners"])
        self.assertNotIn(owner, authority["owners_by_path"][leaf_path])

        for location in ("reconcile", "reason"):
            with self.subTest(location=location):
                mutated = copy.deepcopy(self.grouping)
                group = mutated["groups"][0]
                if location == "reconcile":
                    group["reconcile"]["non_cli_entrypoints"] = {
                        "status": "present",
                        "refs": [cross_artifact_ref],
                        "reason": "",
                    }
                    group["reason"] = {
                        "code": "non_cli_entrypoint",
                        "reference": {
                            "kind": "reconciliation_ref",
                            "dimension": "non_cli_entrypoints",
                            "ref": cross_artifact_ref,
                        },
                        "detail": "The capability remains owned by a non-CLI runtime boundary.",
                    }
                else:
                    group["disposition"] = "remove"
                    group["reason"] = {
                        "code": "separate_removal_review",
                        "reference": {
                            "kind": "m007_08_owner",
                            "value": owner,
                            "artifact_path": leaf_path,
                            "artifact_sha256": leaf_digest,
                        },
                        "detail": "Candidate for a separately reviewed deletion.",
                    }
                with self.assertRaises(cd.CapabilityDispositionError):
                    cd.validate_grouping(
                        mutated,
                        repo_root=ROOT,
                        source_paths=self.sealed["source_paths"],
                        candidate_paths=set(self.candidates),
                        authority=authority,
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
            "un-executed",
            "un executed",
            "un-tested",
            "un tested",
        )
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(cd.CapabilityDispositionError):
                    cd.validate_non_metric_text(value, "reason.detail")

    def test_metric_reason_is_rejected_in_a_group(self) -> None:
        cases = (
            (3, "expose", "The owner has 20 percent of the surface."),
            (0, "retain", "The owner has 20 percent of the surface."),
            (0, "remove", "The owner has 20 percent of the surface."),
            (0, "retain", "Keep this un-executed helper at the runtime boundary."),
            (3, "expose", "Name this un-tested operator surface later."),
            (0, "remove", "Keep this un-tested helper at the runtime boundary."),
            (0, "retain", "Keep this un executed helper at the runtime boundary."),
            (3, "expose", "Name this un tested operator surface later."),
        )
        for index, disposition, detail in cases:
            with self.subTest(disposition=disposition, detail=detail):
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
                        "detail": detail,
                    }
                else:
                    group["reason"]["detail"] = detail
                with self.assertRaises(cd.CapabilityDispositionError):
                    cd.validate_grouping(
                        mutated,
                        repo_root=ROOT,
                        source_paths=self.sealed["source_paths"],
                        candidate_paths=set(self.candidates),
                        authority=self.context["authority"],
                    )

    def test_validate_evidence_does_not_require_dashboard_layout(self) -> None:
        with mock.patch.object(
            cd,
            "validate_dashboard_html",
            side_effect=AssertionError("dashboard layout must not own Met"),
        ):
            result = cd.validate_evidence(ROOT)
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["candidate_member_count"], 93)
        self.assertEqual(result["group_count"], 10)

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

    def test_disposition_definitions_are_present_in_derived_views(self) -> None:
        views = {
            "record": cd.render_html(self.record),
            "dashboard": cd.render_dashboard_html(
                self.record, self.sealed, self.context["authority"]
            ),
            "rollup": cd.render_rollup(self.record, self.sealed),
        }
        for name, view in views.items():
            with self.subTest(view=name):
                if name == "rollup":
                    self.assertIn("## Dispositions", view)
                    self.assertIn("Definitions follow the [accepted M007-09", view)
                else:
                    self.assertIn("What expose, retain, and remove mean", view)
                    self.assertIn("Open the accepted M007-09 proposal", view)
                for _disposition, meaning in cd.DISPOSITION_DEFINITIONS:
                    self.assertIn(meaning, view)

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
        self.assertEqual(
            [coverage_class["id"] for coverage_class in projection["coverage_overview"]["classes"]],
            [
                "discover-observe",
                "perception-workflows",
                "memory-behavior",
                "memory-recovery",
                "physical-qualification",
            ],
        )
        self.assertEqual(
            [coverage_class["status"] for coverage_class in projection["coverage_overview"]["classes"]],
            ["covered", "not_covered", "not_covered", "not_covered", "blocked"],
        )
        self.assertEqual(
            [
                sequence["id"]
                for coverage_class in projection["coverage_overview"]["classes"]
                for sequence in coverage_class["sequences"]
            ],
            [
                sequence["id"]
                for sequence in self.context["authority"]["documents"][
                    "sequence_registry"
                ]["sequences"]
            ],
        )
        self.assertEqual(
            projection["coverage_overview"]["classes"][1]["next_steps"][0],
            {
                "sequence_id": "US-03",
                "owner": "cli-perception-offline",
                "unlock": (
                    "Exact-step #88 US-03 evidence (visual_observer apply + compare) "
                    "after citation amendment; family aggregate is not enough"
                ),
            },
        )
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

    def test_dashboard_command_tree_preserves_recursive_sequence_status(self) -> None:
        projection = cd._dashboard_projection(self.record, self.sealed)
        tree = projection["command_tree"]

        def find(command: str, node: dict) -> dict:
            if node["command"] == command:
                return node
            for child in node["children"]:
                try:
                    return find(command, child)
                except AssertionError:
                    continue
            raise AssertionError(f"missing command tree node: {command}")

        self.assertEqual(tree["command"], "automa")
        self.assertEqual(tree["status"], "partial")
        self.assertEqual(
            [child["token"] for child in tree["children"]],
            ["help", "simulators", "vehicles"],
        )
        self.assertEqual(find("automa help", tree)["status"], "covered")
        self.assertEqual(find("automa simulators", tree)["status"], "uncovered")
        self.assertEqual(
            find("automa vehicles perception compare", tree)["status"],
            "planned",
        )
        self.assertEqual(
            find("automa vehicles perception qualify", tree)["status"],
            "blocked",
        )

    def test_dashboard_command_detail_maps_branch_metadata(self) -> None:
        authority = self.context["authority"]
        html = cd.render_dashboard_html(self.record, self.sealed, authority)
        self.assertIn(
            'id="command-detail" data-initial-command-path="automa"',
            html,
        )
        self.assertIn(
            '<div class="command-selection" id="command-selection" aria-live="polite">'
            '<span class="muted">CLI command:</span> <code>./cli/automa</code></div>',
            html,
        )
        self.assertLess(
            html.index('<div class="command-selection"'),
            html.index('<div class="command-explorer-layout">'),
        )
        self.assertIn(
            'const commandSelection = document.getElementById("command-selection");',
            html,
        )
        self.assertIn('function commandSelectionMarkup(node)', html)
        self.assertIn('commandSelection.innerHTML = commandSelectionMarkup(node);', html)
        self.assertNotIn('command-detail-command', html)
        self.assertEqual(
            cd._dashboard_cli_command("automa vehicles status"),
            "./cli/automa vehicles status",
        )
        self.assertIn("Sequences touching this subtree", html)
        self.assertIn("US-01", html)
        self.assertIn("Exact sequence evidence is present.", html)
        self.assertIn("Owner: <code>cli-perception-offline</code>", html)
        self.assertIn("Uncovered leaves:", html)
        self.assertIn(
            '<button type="button" class="command-node command-node-leaf ',
            html,
        )
        self.assertNotIn('<span class="command-node command-node-leaf', html)
        self.assertIn('data-command-path="automa help"', html)
        self.assertIn('querySelectorAll("button.command-node")', html)
        self.assertIn('closest("button.command-node")', html)

    def test_dashboard_uses_two_column_sections_and_top_navigation(self) -> None:
        html = cd.render_dashboard_html(self.record, self.sealed, self.context["authority"])
        self.assertIn('id="dashboard-toc"', html)
        self.assertIn('data-dashboard-toc="#command-explorer-heading"', html)
        self.assertIn('data-dashboard-toc="#coverage-map-heading"', html)
        self.assertIn('data-dashboard-toc="#source-capability-heading"', html)
        self.assertIn("Coverage by intended operator outcome", html)
        self.assertIn("Source capability and disposition", html)
        self.assertIn('class="coverage-explorer-layout"', html)
        self.assertIn('class="source-explorer-layout"', html)
        self.assertIn('<details class="dashboard-explainer">', html)
        self.assertIn('<details class="section-explainer">', html)
        self.assertIn('<details class="source-explorer-disclosure">', html)
        self.assertIn('more uncovered leaves</summary>', html)

    def test_dashboard_coverage_class_omission_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.html"
            path.write_text(
                cd.render_dashboard_html(self.record, self.sealed, self.context["authority"]),
                encoding="utf-8",
            )
            source = path.read_text(encoding="utf-8")
            marker = '<button type="button" class="coverage-class-row"'
            start = source.index(marker)
            end = source.index(">", start)
            button = source[start:end]
            class_id = "discover-observe"
            button = button.replace(f' data-coverage-class-id="{class_id}"', "")
            path.write_text(source[:start] + button + source[end:], encoding="utf-8")
            with self.assertRaises(cd.CapabilityDispositionError):
                cd.validate_dashboard_html(path, self.record, self.sealed)

    def test_dashboard_command_tree_omission_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dashboard.html"
            path.write_text(
                cd.render_dashboard_html(self.record, self.sealed, self.context["authority"]),
                encoding="utf-8",
            )
            source = path.read_text(encoding="utf-8")
            marker = '<li class="command-tree-node" data-command-path="automa"'
            start = source.index(marker)
            end = source.index(">", start)
            node = source[start:end]
            node = node.replace(' data-command-path="automa"', "")
            path.write_text(source[:start] + node + source[end:], encoding="utf-8")
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
