# Quantitative Change Analysis

- schema: `qca/report/v1`
- mode: `diff`
- analyzer: `0.3.0`
- base: `a8c4483dea577afdb78094caa36d650f47882a27`
- head: `2b74cb8004fb8f99dcc06324ee04b92f80e50a1a`
- working-tree digest: `(none)`
- path: `.`

## Snapshot

| Metric | Value |
| --- | ---: |
| files | 707 |
| included files | 359 |
| raw LOC | 134172 |
| effective LOC | 102245 |
| logical LOC | 47125 |
| decision burden | 11647 |
| callables | 3701 |
| import edges | 2050 |
| public symbols | 1753 |

### Snapshot by source class

| Source class | Files | All raw LOC | Core Python effective LOC |
| --- | ---: | ---: | ---: |
| `docs/configuration` | 85 | 45143 | 0 |
| `experimental/lab` | 28 | 1822 | 1397 |
| `generated/runtime` | 256 | 149360 | 0 |
| `production` | 154 | 66067 | 45450 |
| `tests` | 123 | 37738 | 33916 |
| `tooling/scripts` | 54 | 28545 | 21482 |
| `vendored/minified` | 7 | 250 | 0 |

## Change

| Metric | Value |
| --- | ---: |
| changed files | 9 |
| included changed files | 7 |
| changed directories | 3 |
| added lines | 4775 |
| deleted lines | 20 |
| churn | 4795 |
| included added lines | 3782 |
| included deleted lines | 20 |
| included churn | 3802 |
| decision burden delta | +652 |
| new import edges | 66 |
| public symbols added | 42 |
| public symbols removed | 0 |

### Changed files

| Path | Source class | Core measured | Added | Deleted |
| --- | --- | :---: | ---: | ---: |
| `cli/automa_cli/automation.py` | `production` | yes | 63 | 0 |
| `cli/automa_cli/decision.py` | `production` | yes | 44 | 2 |
| `cli/automa_cli/decision_view.html` | `production` | yes | 390 | 17 |
| `cli/automa_cli/decision_view.py` | `production` | yes | 2260 | 0 |
| `cli/automa_cli/perception_view.py` | `production` | yes | 229 | 1 |
| `docs/milestones/006-decision-facing-perception-readiness/proposals/IMPLEMENTATION-SPEC.md` | `docs/configuration` | no | 415 | 0 |
| `docs/milestones/006-decision-facing-perception-readiness/proposals/live-decision-view.md` | `docs/configuration` | no | 578 | 0 |
| `tests/cli/decision/live_view_fixture.py` | `tests` | yes | 301 | 0 |
| `tests/cli/decision/test_live_decision_view.py` | `tests` | yes | 495 | 0 |

### Changed by source class

| Source class | Files | Added | Deleted |
| --- | ---: | ---: | ---: |
| `docs/configuration` | 2 | 993 | 0 |
| `production` | 5 | 2986 | 20 |
| `tests` | 2 | 796 | 0 |

### Review targets

These deterministic prompts identify evidence to inspect; they are not quality grades or gates.

- inspect `cli/automa_cli/automation.py::run_vehicle_automation`: changed lines overlap callable, logical size increased by 3, decision count increased by 1
- inspect `cli/automa_cli/automation.py::run_vehicle_automation.process_frame`: changed lines overlap callable, logical size increased by 4, decision count increased by 6
- inspect `cli/automa_cli/decision.py::_format_decision_info`: changed lines overlap callable, logical size increased by 5, decision count increased by 1
- inspect `cli/automa_cli/decision.py::get_vehicle_decision_info`: changed lines overlap callable, logical size increased by 4, decision count increased by 4
- inspect `cli/automa_cli/perception_view.py::PerceptionViewServer.__init__`: changed lines overlap callable, logical size increased by 7, decision count increased by 8, maximum nesting increased by 2
- inspect `cli/automa_cli/perception_view.py::PerceptionViewServer.describe`: changed lines overlap callable, logical size increased by 5, decision count increased by 2, maximum nesting increased by 2
- inspect `cli/automa_cli/perception_view.py::PerceptionViewServer.publish_frame`: changed lines overlap callable, logical size increased by 4, decision count increased by 2
- inspect `cli/automa_cli/perception_view.py::PerceptionViewServer.stop`: changed lines overlap callable, logical size increased by 4, decision count increased by 2, maximum nesting increased by 1
- inspect `cli/automa_cli/perception_view.py::_PerceptionViewHandler._handle_request`: changed lines overlap callable, logical size increased by 30, decision count increased by 9
- inspect callables in `cli/automa_cli/automation.py`: callables added in this file
- inspect callables in `cli/automa_cli/decision_view.py`: callables added in this file
- inspect callables in `cli/automa_cli/perception_view.py`: callables added in this file
- inspect callables in `tests/cli/decision/live_view_fixture.py`: callables added in this file
- inspect callables in `tests/cli/decision/test_live_decision_view.py`: callables added in this file
- inspect dependency `cli/automa_cli/automation.py->.decision_view`: new import edge
- inspect dependency `cli/automa_cli/decision.py->.decision_view`: new import edge
- inspect dependency `cli/automa_cli/decision_view.py->.decision`: new import edge
- inspect dependency `cli/automa_cli/decision_view.py->PIL`: new import edge
- inspect dependency `cli/automa_cli/decision_view.py->autonomy.decision`: new import edge
- inspect dependency `cli/automa_cli/perception_view.py->.decision_view`: new import edge
- inspect dependency `cli/automa_cli/perception_view.py->autonomy.decision`: new import edge
- inspect dependency `tests/cli/decision/live_view_fixture.py->PIL`: new import edge
- inspect dependency `tests/cli/decision/live_view_fixture.py->autonomy.decision.decision_data`: new import edge
- inspect dependency `tests/cli/decision/live_view_fixture.py->autonomy.decision.memory`: new import edge
- inspect dependency `tests/cli/decision/live_view_fixture.py->autonomy.decision.observation`: new import edge
- ... 30 additional targets are in the JSON report

## Factors

Static findings are refactoring candidates. Runtime evidence is reported separately.

### contracts

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| cli_argument_count | 408 | 5 |
| cli_command_count | 83 | 0 |
| public_callable_count | 1934 | 51 |
| return_shape_count | 157 | 4 |
| surface_count | 2425 | 56 |

- `cli/automa_cli/automation.py:102`: Public callable contract measured: run_vehicle_automation
- `cli/automa_cli/automation.py:1141`: Public callable contract measured: start_vehicle_automation_background
- `cli/automa_cli/automation.py:1421`: Public callable contract measured: record_vehicle_automation_terminal_result
- `cli/automa_cli/automation.py:1752`: Public callable contract measured: get_vehicle_automation_status
- `cli/automa_cli/automation.py:1806`: Public callable contract measured: stop_vehicle_automation
- `cli/automa_cli/automation.py:1911`: Public callable contract measured: restart_vehicle_automation
- `cli/automa_cli/decision.py:339`: Public callable contract measured: available_decision_engine_ids
- `cli/automa_cli/decision.py:343`: Public callable contract measured: decision_apply_output_root
- 95 more candidates in JSON/HTML.

- Limit: CLI inventory recognizes literal argparse-style add_argument and add_parser calls; it is not a schema or compatibility proof.
- Limit: Public contracts are static AST approximations; runtime decorators, dispatch, inheritance, and annotations are not evaluated.
- Limit: Returned shapes include only direct dict literals in public callable return statements.

### coupling

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| cycle_count | 2 | 1 |
| cyclic_node_count | 5 | 2 |
| edge_count | 586 | 20 |
| external_import_count | 1780 | 57 |
| fan_in_hotspot_count | 63 | 1 |
| fan_out_hotspot_count | 84 | 3 |
| max_fan_in | 49 | 1 |
| max_fan_out | 18 | 0 |
| module_count | 285 | 3 |

- `cli/automa_cli/automation.py:1`: Import is not resolved to a supplied local module: __future__:annotations
- `cli/automa_cli/automation.py:1`: Local dependency fan-in is 9 modules.
- `cli/automa_cli/automation.py:1`: Local dependency fan-out is 14 modules.
- `cli/automa_cli/automation.py:3`: Import is not resolved to a supplied local module: json
- `cli/automa_cli/automation.py:4`: Import is not resolved to a supplied local module: os
- `cli/automa_cli/automation.py:5`: Import is not resolved to a supplied local module: queue
- `cli/automa_cli/automation.py:6`: Import is not resolved to a supplied local module: shlex
- `cli/automa_cli/automation.py:7`: Import is not resolved to a supplied local module: signal
- 109 more candidates in JSON/HTML.

- Limit: Cycles are deterministic strongly connected components, not every distinct runtime import path.
- Limit: Import resolution is AST-based and does not execute module search hooks or dynamic imports.
- Limit: Unresolved imports are separate observations; installability and runtime availability are not measured.

### end_to_end

Status: `not_measured`


- Limit: No subprocess or integration-run evidence was supplied; static source inspection cannot establish end-to-end behavior.

### functional_style

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| attribute_write_count | 667 | 57 |
| global_or_nonlocal_count | 4 | 0 |
| mutable_default_count | 1 | 0 |
| mutating_call_count | 1376 | 18 |
| recognized_effect_count | 2050 | 75 |

- `cli/automa_cli/automation.py:754`: Mutating method call observed: append.
- `cli/automa_cli/automation.py:1290`: Mutating method call observed: append.
- `cli/automa_cli/automation.py:1292`: Mutating method call observed: append.
- `cli/automa_cli/automation.py:1294`: Mutating method call observed: append.
- `cli/automa_cli/automation.py:1343`: Mutating method call observed: close.
- `cli/automa_cli/automation.py:1393`: Mutating method call observed: append.
- `cli/automa_cli/automation.py:1410`: Mutating method call observed: extend.
- `cli/automa_cli/automation.py:2092`: Mutating method call observed: append.
- 147 more candidates in JSON/HTML.

- Limit: Absence of recognized effects does not prove purity.

### functionality

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| stub_count | 16 | 0 |
| unreachable_count | 0 | 0 |


- Limit: Inspect intentional hooks and protocols before removing code.

### lifecycle

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| calls_count | 151 | 14 |
| cleanup_call_count | 33 | 4 |
| cleanup_definition_count | 8 | 1 |
| cleanup_site_count | 41 | 5 |
| definitions_count | 48 | 2 |
| effect_site_count | 199 | 16 |
| parse_error_count | 0 | 0 |
| recognized_site_count | 199 | 16 |
| reset_call_count | 38 | 3 |
| reset_definition_count | 22 | 0 |
| reset_site_count | 60 | 3 |
| site_count | 128 | 0 |
| start_call_count | 50 | 1 |
| start_definition_count | 6 | 0 |
| start_site_count | 56 | 1 |
| stop_call_count | 30 | 6 |
| stop_definition_count | 12 | 1 |
| stop_site_count | 42 | 7 |

- `cli/automa_cli/automation.py:305`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/automation.py:804`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/automation.py:2469`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/automation.py:433`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/automation.py:802`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/automation.py:1343`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/decision_view.py:722`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/decision_view.py:725`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- 7 more candidates in JSON/HTML.

- Limit: Recognized names and call sites are static indicators only; they do not prove start/stop/reset/cleanup effects or symmetry.
- Limit: No runtime lifecycle sequence, resource ownership, or teardown outcome was measured.
- Limit: Lifecycle site details are limited to the first 128; counts remain parser-derived.

### patterns

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| bare_except_count | 2 | 0 |
| broad_except_count | 81 | 4 |
| logged_error_count | 3 | 0 |
| raise_count | 1597 | 112 |
| swallowed_exception_count | 31 | 3 |

- `cli/automa_cli/automation.py:211`: Broad except for Exception or BaseException observed.
- `cli/automa_cli/automation.py:453`: Except body is only pass; exception may be swallowed.
- `cli/automa_cli/automation.py:480`: Broad except for Exception or BaseException observed.
- `cli/automa_cli/automation.py:502`: Except body is only pass; exception may be swallowed.
- `cli/automa_cli/automation.py:510`: Raise statement observed.
- `cli/automa_cli/automation.py:569`: Broad except for Exception or BaseException observed.
- `cli/automa_cli/automation.py:579`: Broad except for Exception or BaseException observed.
- `cli/automa_cli/automation.py:579`: Except body is only pass; exception may be swallowed.
- 301 more candidates in JSON/HTML.

- Limit: Inspect intent at the owning boundary; recognized patterns are not a style grade.

### redundancy

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| clone_group_count | 58 | 1 |
| cloned_callable_count | 165 | 8 |
| duplicate_ast_loc | 247 | 7 |
| repeated_branch_count | 0 | 0 |

- `autonomy/decision/activation.py:698`: Nontrivial callable body is shared by 14 callables: _timestamp_ms, timestamp_ms, timestamp_ms, _timestamp_ms, timestamp_ms, _timestamp_ms, _timestamp_ms, _timestamp_ms, _now_ms, _now_ms, timestamp_ms, _timestamp_ms, _timestamp_ms, timestamp_ms.
- `cli/automa_cli/automation.py:2341`: Nontrivial callable body is shared by 2 callables: _manifest_get_str, _manifest_get_str.
- `cli/automa_cli/automation.py:2349`: Nontrivial callable body is shared by 2 callables: _manifest_get_dict, _manifest_get_dict.
- `cli/automa_cli/automation.py:2373`: Nontrivial callable body is shared by 2 callables: _int_or_none, _int_or_none.
- `cli/automa_cli/automation.py:2548`: Nontrivial callable body is shared by 2 callables: _pid_alive, _process_alive.
- `cli/automa_cli/automation.py:2638`: Nontrivial callable body is shared by 6 callables: _emit, _emit, _emit, _emit, _emit, _emit.
- `cli/automa_cli/decision.py:339`: Nontrivial callable body is shared by 3 callables: available_decision_engine_ids, available_memory_implementation_ids, available_perception_algorithm_ids.
- `cli/automa_cli/decision.py:2115`: Nontrivial callable body is shared by 2 callables: _load_state, default_load_latest.
- 2 more candidates in JSON/HTML.

- Limit: Identifier normalization is approximate; renamed locals may still look identical.

### test_effectiveness

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| assertion_count | 4184 | 172 |
| candidate_assertion_count | 0 | 0 |
| candidate_site_count | 0 | 0 |
| literal_assertion_candidates | 0 | 0 |
| parse_error_count | 0 | 0 |
| python_file_count | 285 | 3 |
| source_file_count | 285 | 3 |
| tautological_assertion_candidates | 0 | 0 |
| test_case_count | 1031 | 20 |
| test_file_count | 117 | 2 |


- Limit: Literal and same-operand assertions are candidates for review, not a judgment that a test is ineffective.
- Limit: Static inspection does not infer input variation, mocks, explicit state setup, or the behavior under test.

### ui_behavior

Status: `not_measured`


- Limit: No actual browser interaction evidence was supplied; loopback/API traces alone do not establish UI behavior.

## Observations

- `snapshot.files`: 707
- `snapshot.included_files`: 359
- `snapshot.raw_loc`: 134172
- `snapshot.effective_loc`: 102245
- `snapshot.logical_loc`: 47125
- `snapshot.decision_burden`: 11647
- `snapshot.unsupported_files`: [".coveragerc", ".github/PULL_REQUEST_TEMPLATE/implementation-adjunct.md", ".github/PULL_REQUEST_TEMPLATE/milestone.md", ".github/PULL_REQUEST_TEMPLATE/proposal-amendment.md", ".github/PULL_REQUEST_TEMPLATE/proposal.md", ".github/PULL_REQUEST_TEMPLATE/repair.md", ".github/pull_request_template.md", ".github/workflows/tests.yml", ".gitignore", "cli/automa", "cli/automa_cli/decision_view.html", "cli/automa_cli/memory_view.html", "cli/automa_cli/perception_view.html", "cli/automa_cli/workbench.html", "deploy/targets/donkeycar/app/automa_drive.sh", "deploy/targets/donkeycar/patches/waveshare-donkeycar-local.patch", "deploy/targets/donkeycar/systemd/automa-donkey.service.in", "deploy/targets/donkeycar/systemd/control.sh", "deploy/targets/donkeycar/systemd/install.sh", "docs/milestones/007-cli-operator-usability/tools/capability-disposition/README.md", "docs/milestones/007-cli-operator-usability/tools/capability-disposition/grouping.json", "docs/milestones/007-cli-operator-usability/tools/capability-disposition/source_analysis.json", "docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/README.md", "docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/coverage_session", "docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/manifest.json", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/README.md", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/claim_map.json", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/leaf_inventory.json", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/leaf_overlay.json", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/live_residuals.json", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/sequence_registry.json", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/us88_catalog.json", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/us88_catalog.sha256", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/us88_source.md", "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/us88_source.sha256", "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/README.md", "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/exploratory-discovery.yaml", "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/m007-acceptance.yaml", "docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/m007-continuity.yaml", "frontend/donkeycar/index.html", "frontend/donkeycar/start.sh", "frontend/donkeycar/static/bootstrap/3.3.7/fonts/glyphicons-halflings-regular.ttf", "frontend/donkeycar/static/bootstrap/3.3.7/fonts/glyphicons-halflings-regular.woff", "frontend/donkeycar/static/bootstrap/3.3.7/fonts/glyphicons-halflings-regular.woff2", "frontend/donkeycar/static/donkeycar-logo-sideways.png", "frontend/donkeycar/static/img_placeholder.jpg", "frontend/donkeycar/static/main.js", "frontend/donkeycar/static/nipple.js", "frontend/donkeycar/static/style.css", "lab/plugins/README.md", "lab/plugins/perception/README.md", "lab/plugins/perception/classical_regions/README.md", "lab/plugins/perception/classical_regions/plugin.json", "lab/plugins/perception/fastsam/README.md", "lab/plugins/perception/fastsam/plugin.json", "lab/plugins/perception/fastsam/requirements.txt", "lab/plugins/perception/floor_continuity/README.md", "lab/plugins/perception/floor_continuity/plugin.json", "lab/plugins/perception/floor_continuity_capture/README.md", "lab/plugins/perception/floor_continuity_capture/plugin.json", "qca/experiments/candidates/combine_actions.patch", "qca/experiments/candidates/path_containment.patch", "qca/experiments/candidates/share_now_ms.patch", "qca/experiments/candidates/skip_validation.patch", "qca/experiments/candidates/stale_run_error.patch", "qca/experiments/candidates/symlink_within.patch", "requirements-test.txt", "requirements.txt", "tests/README.md", "tests/cli/decision/fixtures/apply_active_left/sequence.json", "tests/cli/decision/fixtures/apply_no_memory/sequence.json", "tests/cli/decision/fixtures/apply_two_frames/sequence.json", "tests/cli/memory/fixtures/conflict_sequence.json", "tests/cli/memory/fixtures/recurrence_sequence.json"]
- `snapshot.syntax_errors`: []
- `diff.changed_files`: ["cli/automa_cli/automation.py", "cli/automa_cli/decision.py", "cli/automa_cli/decision_view.html", "cli/automa_cli/decision_view.py", "cli/automa_cli/perception_view.py", "docs/milestones/006-decision-facing-perception-readiness/proposals/IMPLEMENTATION-SPEC.md", "docs/milestones/006-decision-facing-perception-readiness/proposals/live-decision-view.md", "tests/cli/decision/live_view_fixture.py", "tests/cli/decision/test_live_decision_view.py"]
- `diff.included_changed_files`: ["cli/automa_cli/automation.py", "cli/automa_cli/decision.py", "cli/automa_cli/decision_view.html", "cli/automa_cli/decision_view.py", "cli/automa_cli/perception_view.py", "tests/cli/decision/live_view_fixture.py", "tests/cli/decision/test_live_decision_view.py"]
- `diff.changed_directory_count`: 3
- `diff.added_lines`: 4775
- `diff.deleted_lines`: 20
- `diff.churn`: 4795
- `diff.included_added_lines`: 3782
- `diff.included_deleted_lines`: 20
- `diff.included_churn`: 3802
- `diff.decision_burden_delta`: 652
- `diff.new_import_edges`: ["cli/automa_cli/automation.py->.decision_view", "cli/automa_cli/decision.py->.decision_view", "cli/automa_cli/decision_view.py->.decision", "cli/automa_cli/decision_view.py->PIL", "cli/automa_cli/decision_view.py->__future__", "cli/automa_cli/decision_view.py->autonomy.decision", "cli/automa_cli/decision_view.py->collections", "cli/automa_cli/decision_view.py->copy", "cli/automa_cli/decision_view.py->dataclasses", "cli/automa_cli/decision_view.py->hashlib", "cli/automa_cli/decision_view.py->io", "cli/automa_cli/decision_view.py->json", "cli/automa_cli/decision_view.py->math", "cli/automa_cli/decision_view.py->os", "cli/automa_cli/decision_view.py->pathlib", "cli/automa_cli/decision_view.py->re", "cli/automa_cli/decision_view.py->threading", "cli/automa_cli/decision_view.py->time", "cli/automa_cli/decision_view.py->typing", "cli/automa_cli/decision_view.py->urllib.error", "cli/automa_cli/decision_view.py->urllib.parse", "cli/automa_cli/decision_view.py->urllib.request", "cli/automa_cli/perception_view.py->.decision_view", "cli/automa_cli/perception_view.py->autonomy.decision", "tests/cli/decision/live_view_fixture.py->PIL", "tests/cli/decision/live_view_fixture.py->__future__", "tests/cli/decision/live_view_fixture.py->argparse", "tests/cli/decision/live_view_fixture.py->autonomy.decision.decision_data", "tests/cli/decision/live_view_fixture.py->autonomy.decision.memory", "tests/cli/decision/live_view_fixture.py->autonomy.decision.observation", "tests/cli/decision/live_view_fixture.py->autonomy.perception", "tests/cli/decision/live_view_fixture.py->cli.automa_cli.decision", "tests/cli/decision/live_view_fixture.py->cli.automa_cli.decision_view", "tests/cli/decision/live_view_fixture.py->cli.automa_cli.perception_view", "tests/cli/decision/live_view_fixture.py->implementations.decision.catalog", "tests/cli/decision/live_view_fixture.py->io", "tests/cli/decision/live_view_fixture.py->json", "tests/cli/decision/live_view_fixture.py->os", "tests/cli/decision/live_view_fixture.py->pathlib", "tests/cli/decision/live_view_fixture.py->re", "tests/cli/decision/live_view_fixture.py->shlex", "tests/cli/decision/live_view_fixture.py->signal", "tests/cli/decision/live_view_fixture.py->sys", "tests/cli/decision/live_view_fixture.py->tests.support.cli_runner", "tests/cli/decision/live_view_fixture.py->threading", "tests/cli/decision/live_view_fixture.py->time", "tests/cli/decision/test_live_decision_view.py->__future__", "tests/cli/decision/test_live_decision_view.py->autonomy.decision.memory", "tests/cli/decision/test_live_decision_view.py->cli.automa_cli.decision", "tests/cli/decision/test_live_decision_view.py->cli.automa_cli.decision_view", "tests/cli/decision/test_live_decision_view.py->copy", "tests/cli/decision/test_live_decision_view.py->dataclasses", "tests/cli/decision/test_live_decision_view.py->hashlib", "tests/cli/decision/test_live_decision_view.py->http.client", "tests/cli/decision/test_live_decision_view.py->json", "tests/cli/decision/test_live_decision_view.py->os", "tests/cli/decision/test_live_decision_view.py->pathlib", "tests/cli/decision/test_live_decision_view.py->select", "tests/cli/decision/test_live_decision_view.py->subprocess", "tests/cli/decision/test_live_decision_view.py->sys", "tests/cli/decision/test_live_decision_view.py->tempfile", "tests/cli/decision/test_live_decision_view.py->tests.cli.decision.live_view_fixture", "tests/cli/decision/test_live_decision_view.py->tests.support.cli_runner", "tests/cli/decision/test_live_decision_view.py->time", "tests/cli/decision/test_live_decision_view.py->unittest", "tests/cli/decision/test_live_decision_view.py->urllib.parse"]
- `diff.public_symbols_added`: ["cli/automa_cli/automation.py:MAX_DECISION_FILE_BYTES", "cli/automa_cli/decision_view.py:DECISION_API_PATH", "cli/automa_cli/decision_view.py:DECISION_IMAGE_PATH", "cli/automa_cli/decision_view.py:DECISION_VIEW_ID", "cli/automa_cli/decision_view.py:DECISION_VIEW_PATH", "cli/automa_cli/decision_view.py:DECISION_VIEW_SCHEMA", "cli/automa_cli/decision_view.py:DecisionViewHTTPError", "cli/automa_cli/decision_view.py:DecisionViewPublisher", "cli/automa_cli/decision_view.py:GENERATION_RE", "cli/automa_cli/decision_view.py:HOST_OBSERVATION_SCHEMA", "cli/automa_cli/decision_view.py:IMAGE_CONTENT_TYPES", "cli/automa_cli/decision_view.py:IMAGE_ID_RE", "cli/automa_cli/decision_view.py:MAX_DECISION_FILE_BYTES", "cli/automa_cli/decision_view.py:MAX_IMAGES", "cli/automa_cli/decision_view.py:MAX_IMAGE_BYTES", "cli/automa_cli/decision_view.py:MAX_METADATA_BYTES", "cli/automa_cli/decision_view.py:MAX_PIXELS_PER_IMAGE", "cli/automa_cli/decision_view.py:MAX_POLYGON_VERTICES", "cli/automa_cli/decision_view.py:MAX_RECORD_BYTES", "cli/automa_cli/decision_view.py:MAX_RESPONSE_BYTES", "cli/automa_cli/decision_view.py:MAX_SAFE_INT", "cli/automa_cli/decision_view.py:MAX_TOTAL_IMAGE_BYTES", "cli/automa_cli/decision_view.py:activation_sha256", "cli/automa_cli/decision_view.py:default_limits", "cli/automa_cli/decision_view.py:generation_for_identity", "cli/automa_cli/decision_view.py:identity_for_activation", "cli/automa_cli/decision_view.py:parse_generation_query", "cli/automa_cli/decision_view.py:parse_image_id", "cli/automa_cli/decision_view.py:probe_decision_view", "cli/automa_cli/decision_view.py:timestamp_ms", "cli/automa_cli/perception_view.py:DECISION_VIEW_HTML_PATH", "tests/cli/decision/live_view_fixture.py:DecisionFixture", "tests/cli/decision/live_view_fixture.py:LABEL", "tests/cli/decision/live_view_fixture.py:LEFT_BOX", "tests/cli/decision/live_view_fixture.py:REPOSITORY", "tests/cli/decision/live_view_fixture.py:RIGHT_BOX", "tests/cli/decision/live_view_fixture.py:SCENARIOS", "tests/cli/decision/live_view_fixture.py:main", "tests/cli/decision/live_view_fixture.py:synthetic_png", "tests/cli/decision/live_view_fixture.py:write_json", "tests/cli/decision/test_live_decision_view.py:DisposableFixtureProcessTests", "tests/cli/decision/test_live_decision_view.py:LiveDecisionViewTests"]
- `diff.public_symbols_removed`: []
