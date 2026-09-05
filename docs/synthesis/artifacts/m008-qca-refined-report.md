# Quantitative Change Analysis

- schema: `qca/report/v1`
- mode: `diff`
- analyzer: `0.3.0`
- base: `897fef80f4a5d39ea24715a6df56e7314256fabe`
- head: `3fce449d1eb64d408458231163c3f8b9b5c23af3`
- working-tree digest: `(none)`
- path: `.`

## Snapshot

| Metric | Value |
| --- | ---: |
| files | 642 |
| included files | 243 |
| raw LOC | 77008 |
| effective LOC | 66369 |
| logical LOC | 29671 |
| decision burden | 5840 |
| callables | 2574 |
| import edges | 1486 |
| public symbols | 918 |

### Snapshot by source class

| Source class | Files | All raw LOC | Core Python effective LOC |
| --- | ---: | ---: | ---: |
| `docs/configuration` | 69 | 28886 | 0 |
| `experimental/lab` | 28 | 1822 | 1395 |
| `generated/runtime` | 255 | 126601 | 0 |
| `production` | 123 | 52470 | 32298 |
| `tests` | 106 | 31999 | 28955 |
| `tooling/scripts` | 54 | 28545 | 3721 |
| `vendored/minified` | 7 | 250 | 0 |

## Change

| Metric | Value |
| --- | ---: |
| changed files | 40 |
| included changed files | 22 |
| changed directories | 15 |
| added lines | 12828 |
| deleted lines | 67 |
| churn | 12895 |
| included added lines | 7788 |
| included deleted lines | 67 |
| included churn | 7855 |
| decision burden delta | +593 |
| new import edges | 111 |
| public symbols added | 60 |
| public symbols removed | 0 |

### Changed files

| Path | Source class | Core measured | Added | Deleted |
| --- | --- | :---: | ---: | ---: |
| `cli/automa_cli/app.py` | `production` | yes | 137 | 0 |
| `cli/automa_cli/loopback_http.py` | `production` | yes | 133 | 0 |
| `cli/automa_cli/perception_view.py` | `production` | yes | 21 | 66 |
| `cli/automa_cli/workbench.html` | `production` | yes | 2026 | 0 |
| `cli/automa_cli/workbench.py` | `production` | yes | 204 | 0 |
| `cli/automa_cli/workbench_contract.py` | `production` | yes | 68 | 0 |
| `cli/automa_cli/workbench_plugins.py` | `production` | yes | 712 | 0 |
| `cli/automa_cli/workbench_runner.py` | `production` | yes | 1516 | 0 |
| `cli/automa_cli/workbench_server.py` | `production` | yes | 480 | 0 |
| `cli/automa_cli/workbench_source.py` | `production` | yes | 560 | 0 |
| `docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md` | `docs/configuration` | no | 191 | 0 |
| `docs/milestones/008-cli-decision-workbench/closeout.md` | `docs/configuration` | no | 201 | 0 |
| `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/README.md` | `generated/runtime` | no | 90 | 0 |
| `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/browser-view.png` | `generated/runtime` | no | 0 | 0 |
| `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/cli-transcript.txt` | `generated/runtime` | no | 13 | 0 |
| `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/record_session.py` | `tooling/scripts` | no | 1301 | 0 |
| `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/render_result.py` | `tooling/scripts` | no | 198 | 0 |
| `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/result.html` | `generated/runtime` | no | 89 | 0 |
| `docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/result.json` | `generated/runtime` | no | 588 | 0 |
| `docs/milestones/008-cli-decision-workbench/plan.html` | `docs/configuration` | no | 523 | 0 |
| `docs/milestones/008-cli-decision-workbench/plan.md` | `docs/configuration` | no | 160 | 0 |
| `docs/milestones/008-cli-decision-workbench/proposals/closeout.md` | `docs/configuration` | no | 362 | 0 |
| `docs/milestones/008-cli-decision-workbench/proposals/perception-live-plugin-selection-amendment.md` | `docs/configuration` | no | 80 | 0 |
| `docs/milestones/008-cli-decision-workbench/proposals/perception-memory-workbench.md` | `docs/configuration` | no | 376 | 0 |
| `docs/milestones/008-cli-decision-workbench/proposals/perception-plugin-selection-amendment.md` | `docs/configuration` | no | 374 | 0 |
| `docs/milestones/008-cli-decision-workbench/proposals/perception-raw-capture-paused-refresh-amendment.md` | `docs/configuration` | no | 118 | 0 |
| `docs/milestones/008-cli-decision-workbench/proposals/replay-workbench-acceptance.md` | `docs/configuration` | no | 339 | 0 |
| `docs/milestones/completed.md` | `docs/configuration` | no | 37 | 0 |
| `lab/plugins/perception/classical_regions/plugin.json` | `experimental/lab` | yes | 7 | 0 |
| `lab/plugins/perception/fastsam/plugin.json` | `experimental/lab` | yes | 7 | 0 |
| `lab/plugins/perception/floor_continuity/plugin.json` | `experimental/lab` | yes | 7 | 0 |
| `lab/plugins/perception/floor_continuity/src/plugin.py` | `experimental/lab` | yes | 3 | 1 |
| `lab/plugins/perception/floor_continuity_capture/README.md` | `experimental/lab` | yes | 29 | 0 |
| `lab/plugins/perception/floor_continuity_capture/__init__.py` | `experimental/lab` | yes | 1 | 0 |
| `lab/plugins/perception/floor_continuity_capture/plugin.json` | `experimental/lab` | yes | 29 | 0 |
| `lab/plugins/perception/floor_continuity_capture/src/__init__.py` | `experimental/lab` | yes | 1 | 0 |
| `lab/plugins/perception/floor_continuity_capture/src/plugin.py` | `experimental/lab` | yes | 9 | 0 |
| `tests/cli/test_workbench.py` | `tests` | yes | 1259 | 0 |
| `tests/lab/perception/test_floor_continuity_capture.py` | `tests` | yes | 125 | 0 |
| `tests/milestones/test_replay_workbench_record_session.py` | `tests` | yes | 454 | 0 |

### Changed by source class

| Source class | Files | Added | Deleted |
| --- | ---: | ---: | ---: |
| `docs/configuration` | 11 | 2761 | 0 |
| `experimental/lab` | 9 | 93 | 1 |
| `generated/runtime` | 5 | 780 | 0 |
| `production` | 10 | 5857 | 66 |
| `tests` | 3 | 1838 | 0 |
| `tooling/scripts` | 2 | 1499 | 0 |

### Review targets

These deterministic prompts identify evidence to inspect; they are not quality grades or gates.

- inspect `cli/automa_cli/app.py::build_parser`: changed lines overlap callable, logical size increased by 18
- inspect `cli/automa_cli/perception_view.py::PerceptionViewServer.__init__`: changed lines overlap callable, logical size decreased by 1, decision count decreased by 1, maximum nesting decreased by 1
- inspect `cli/automa_cli/perception_view.py::PerceptionViewServer.start`: changed lines overlap callable, logical size decreased by 5, decision count decreased by 2, maximum nesting decreased by 1
- inspect `cli/automa_cli/perception_view.py::PerceptionViewServer.stop`: changed lines overlap callable, logical size decreased by 3, decision count decreased by 1
- inspect `cli/automa_cli/perception_view.py::PerceptionViewServer.url`: changed lines overlap callable, logical size decreased by 2, decision count decreased by 1
- inspect `cli/automa_cli/perception_view.py::_PerceptionViewHandler._send`: callable removed
- inspect `cli/automa_cli/perception_view.py::_PerceptionViewHandler._send_json`: callable removed
- inspect `cli/automa_cli/perception_view.py::_PerceptionViewHandler.log_message`: callable removed
- inspect callables in `cli/automa_cli/app.py`: callables added in this file, changed lines overlap callables; measured shape unchanged
- inspect callables in `cli/automa_cli/loopback_http.py`: callables added in this file
- inspect callables in `cli/automa_cli/workbench.py`: callables added in this file
- inspect callables in `cli/automa_cli/workbench_contract.py`: callables added in this file
- inspect callables in `cli/automa_cli/workbench_plugins.py`: callables added in this file
- inspect callables in `cli/automa_cli/workbench_runner.py`: callables added in this file
- inspect callables in `cli/automa_cli/workbench_server.py`: callables added in this file
- inspect callables in `cli/automa_cli/workbench_source.py`: callables added in this file
- inspect callables in `lab/plugins/perception/floor_continuity/src/plugin.py`: changed lines overlap callables; measured shape unchanged
- inspect callables in `tests/cli/test_workbench.py`: callables added in this file
- inspect callables in `tests/lab/perception/test_floor_continuity_capture.py`: callables added in this file
- inspect callables in `tests/milestones/test_replay_workbench_record_session.py`: callables added in this file
- inspect dependency `cli/automa_cli/app.py->.workbench`: new import edge
- inspect dependency `cli/automa_cli/perception_view.py->.loopback_http`: new import edge
- inspect dependency `cli/automa_cli/workbench.py->.perception_runs`: new import edge
- inspect dependency `cli/automa_cli/workbench.py->.workbench_contract`: new import edge
- inspect dependency `cli/automa_cli/workbench.py->.workbench_plugins`: new import edge
- ... 87 additional targets are in the JSON report

## Factors

Static findings are refactoring candidates. Runtime evidence is reported separately.

### contracts

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| cli_argument_count | 337 | 10 |
| cli_command_count | 67 | 3 |
| public_callable_count | 1494 | 97 |
| return_shape_count | 107 | 7 |
| surface_count | 1898 | 110 |

- `cli/automa_cli/app.py:68`: Public callable contract measured: build_parser
- `cli/automa_cli/app.py:75`: CLI command declaration measured: help
- `cli/automa_cli/app.py:81`: CLI command declaration measured: vehicles
- `cli/automa_cli/app.py:88`: CLI command declaration measured: help
- `cli/automa_cli/app.py:94`: CLI command declaration measured: active
- `cli/automa_cli/app.py:102`: CLI argument declaration measured: --timeout-s
- `cli/automa_cli/app.py:111`: CLI argument declaration measured: --picar-url
- `cli/automa_cli/app.py:117`: CLI argument declaration measured: --chase-ws-url
- 337 more candidates in JSON/HTML.

- Limit: CLI inventory recognizes literal argparse-style add_argument and add_parser calls; it is not a schema or compatibility proof.
- Limit: Public contracts are static AST approximations; runtime decorators, dispatch, inheritance, and annotations are not evaluated.
- Limit: Returned shapes include only direct dict literals in public callable return statements.

### coupling

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| cycle_count | 1 | 0 |
| cyclic_node_count | 3 | 0 |
| edge_count | 439 | 34 |
| external_import_count | 1225 | 85 |
| fan_in_hotspot_count | 49 | 4 |
| fan_out_hotspot_count | 63 | 6 |
| max_fan_in | 43 | 4 |
| max_fan_out | 18 | 1 |
| module_count | 226 | 13 |

- `cli/automa_cli/app.py:1`: Import is not resolved to a supplied local module: __future__:annotations
- `cli/automa_cli/app.py:1`: Local dependency fan-in is 4 modules.
- `cli/automa_cli/app.py:1`: Local dependency fan-out is 18 modules.
- `cli/automa_cli/app.py:3`: Import is not resolved to a supplied local module: argparse
- `cli/automa_cli/app.py:4`: Import is not resolved to a supplied local module: json
- `cli/automa_cli/app.py:5`: Import is not resolved to a supplied local module: math
- `cli/automa_cli/app.py:6`: Import is not resolved to a supplied local module: sys
- `cli/automa_cli/app.py:7`: Import is not resolved to a supplied local module: pathlib:Path
- 123 more candidates in JSON/HTML.

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
| attribute_write_count | 462 | 92 |
| global_or_nonlocal_count | 1 | 0 |
| mutable_default_count | 0 | 0 |
| mutating_call_count | 729 | 31 |
| recognized_effect_count | 1193 | 123 |

- `cli/automa_cli/app.py:2225`: Mutating method call observed: append.
- `cli/automa_cli/loopback_http.py:130`: Mutating method call observed: write.
- `cli/automa_cli/perception_view.py:49`: Attribute write observed: vehicle_id.
- `cli/automa_cli/perception_view.py:50`: Attribute write observed: automation_dir.
- `cli/automa_cli/perception_view.py:51`: Attribute write observed: host.
- `cli/automa_cli/perception_view.py:52`: Attribute write observed: preferred_port.
- `cli/automa_cli/perception_view.py:53`: Attribute write observed: run_id.
- `cli/automa_cli/perception_view.py:54`: Attribute write observed: worker_pid.
- 154 more candidates in JSON/HTML.

- Limit: Absence of recognized effects does not prove purity.

### functionality

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| stub_count | 15 | 6 |
| unreachable_count | 0 | 0 |

- `cli/automa_cli/workbench_server.py:36`: Stub callable observed: server_identity.
- `cli/automa_cli/workbench_server.py:39`: Stub callable observed: state.
- `cli/automa_cli/workbench_server.py:42`: Stub callable observed: close.
- `cli/automa_cli/workbench_server.py:45`: Stub callable observed: dispatch.
- `cli/automa_cli/workbench_server.py:48`: Stub callable observed: frame_detail.
- `cli/automa_cli/workbench_server.py:56`: Stub callable observed: frame_bytes.

- Limit: Inspect intentional hooks and protocols before removing code.

### lifecycle

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| calls_count | 115 | 46 |
| cleanup_call_count | 23 | 9 |
| cleanup_definition_count | 6 | 4 |
| cleanup_site_count | 29 | 13 |
| definitions_count | 42 | 13 |
| effect_site_count | 157 | 59 |
| parse_error_count | 0 | 0 |
| recognized_site_count | 157 | 59 |
| reset_call_count | 34 | 9 |
| reset_definition_count | 21 | 3 |
| reset_site_count | 55 | 12 |
| site_count | 128 | 30 |
| start_call_count | 39 | 25 |
| start_definition_count | 5 | 3 |
| start_site_count | 44 | 28 |
| stop_call_count | 19 | 3 |
| stop_definition_count | 10 | 3 |
| stop_site_count | 29 | 6 |

- `cli/automa_cli/loopback_http.py:31`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/loopback_http.py:41`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/perception_view.py:76`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/perception_view.py:176`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/workbench.py:84`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/workbench.py:82`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/workbench.py:132`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- `cli/automa_cli/workbench.py:134`: Static lifecycle name/call site only; inspect the runtime effect and paired cleanup behavior separately.
- 36 more candidates in JSON/HTML.

- Limit: Recognized names and call sites are static indicators only; they do not prove start/stop/reset/cleanup effects or symmetry.
- Limit: No runtime lifecycle sequence, resource ownership, or teardown outcome was measured.
- Limit: Lifecycle site details are limited to the first 128; counts remain parser-derived.

### patterns

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| bare_except_count | 0 | 0 |
| broad_except_count | 41 | 7 |
| logged_error_count | 0 | 0 |
| raise_count | 545 | 121 |
| swallowed_exception_count | 15 | 3 |

- `cli/automa_cli/loopback_http.py:23`: Raise statement observed.
- `cli/automa_cli/loopback_http.py:72`: Raise statement observed.
- `cli/automa_cli/perception_view.py:98`: Raise statement observed.
- `cli/automa_cli/workbench_plugins.py:139`: Raise statement observed.
- `cli/automa_cli/workbench_plugins.py:141`: Raise statement observed.
- `cli/automa_cli/workbench_plugins.py:144`: Raise statement observed.
- `cli/automa_cli/workbench_plugins.py:147`: Raise statement observed.
- `cli/automa_cli/workbench_plugins.py:149`: Raise statement observed.
- 126 more candidates in JSON/HTML.

- Limit: Inspect intent at the owning boundary; recognized patterns are not a style grade.

### redundancy

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| clone_group_count | 41 | 5 |
| cloned_callable_count | 121 | 12 |
| duplicate_ast_loc | 194 | 8 |
| repeated_branch_count | 0 | 0 |

- `autonomy/decision/activation.py:698`: Nontrivial callable body is shared by 13 callables: _timestamp_ms, timestamp_ms, timestamp_ms, _timestamp_ms, _timestamp_ms, _timestamp_ms, _timestamp_ms, _now_ms, _now_ms, timestamp_ms, _timestamp_ms, _timestamp_ms, timestamp_ms.
- `cli/automa_cli/app.py:1883`: Nontrivial callable body is shared by 4 callables: _handle_vehicles_automation_status, _handle_vehicles_info_perception, _handle_vehicles_info_decision, _handle_vehicles_info_memory.
- `cli/automa_cli/app.py:2055`: Nontrivial callable body is shared by 2 callables: _handle_vehicles_update_core, _handle_vehicles_update_autonomy.
- `lab/plugins/perception/classical_regions/src/plugin.py:246`: Nontrivial callable body is shared by 3 callables: _zone, _zone, _zone.
- `tests/autonomy/perception/test_plugin_runner.py:56`: Nontrivial callable body is shared by 2 callables: reset, reset.
- `tests/cli/test_workbench.py:802`: Nontrivial callable body is shared by 2 callables: post, post.
- `tests/lab/perception/test_floor_continuity.py:98`: Nontrivial callable body is shared by 2 callables: _request, _request.
- `tests/lab/perception/test_floor_continuity.py:102`: Nontrivial callable body is shared by 2 callables: _snapshot, _snapshot.
- 1 more candidates in JSON/HTML.

- Limit: Identifier normalization is approximate; renamed locals may still look identical.

### test_effectiveness

Status: `measured`

| Measurement | Head | Delta |
| --- | ---: | ---: |
| assertion_count | 3358 | 275 |
| candidate_assertion_count | 0 | 0 |
| candidate_site_count | 0 | 0 |
| literal_assertion_candidates | 0 | 0 |
| parse_error_count | 0 | 0 |
| python_file_count | 226 | 13 |
| source_file_count | 226 | 13 |
| tautological_assertion_candidates | 0 | 0 |
| test_case_count | 875 | 39 |
| test_file_count | 103 | 3 |


- Limit: Literal and same-operand assertions are candidates for review, not a judgment that a test is ineffective.
- Limit: Static inspection does not infer input variation, mocks, explicit state setup, or the behavior under test.

### ui_behavior

Status: `not_measured`


- Limit: No actual browser interaction evidence was supplied; loopback/API traces alone do not establish UI behavior.

## Observations

- `snapshot.files`: 642
- `snapshot.included_files`: 243
- `snapshot.raw_loc`: 77008
- `snapshot.effective_loc`: 66369
- `snapshot.logical_loc`: 29671
- `snapshot.decision_burden`: 5840
- `snapshot.unsupported_files`: ["cli/automa", "cli/automa_cli/memory_view.html", "cli/automa_cli/perception_view.html", "cli/automa_cli/workbench.html", "lab/plugins/perception/README.md", "lab/plugins/perception/classical_regions/README.md", "lab/plugins/perception/classical_regions/plugin.json", "lab/plugins/perception/fastsam/README.md", "lab/plugins/perception/fastsam/plugin.json", "lab/plugins/perception/fastsam/requirements.txt", "lab/plugins/perception/floor_continuity/README.md", "lab/plugins/perception/floor_continuity/plugin.json", "lab/plugins/perception/floor_continuity_capture/README.md", "lab/plugins/perception/floor_continuity_capture/plugin.json", "tests/README.md", "tests/cli/memory/fixtures/conflict_sequence.json", "tests/cli/memory/fixtures/recurrence_sequence.json"]
- `snapshot.syntax_errors`: []
- `diff.changed_files`: ["cli/automa_cli/app.py", "cli/automa_cli/loopback_http.py", "cli/automa_cli/perception_view.py", "cli/automa_cli/workbench.html", "cli/automa_cli/workbench.py", "cli/automa_cli/workbench_contract.py", "cli/automa_cli/workbench_plugins.py", "cli/automa_cli/workbench_runner.py", "cli/automa_cli/workbench_server.py", "cli/automa_cli/workbench_source.py", "docs/milestones/008-cli-decision-workbench/assessment/perception-memory-workbench.md", "docs/milestones/008-cli-decision-workbench/closeout.md", "docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/README.md", "docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/browser-view.png", "docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/cli-transcript.txt", "docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/record_session.py", "docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/render_result.py", "docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/result.html", "docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/result.json", "docs/milestones/008-cli-decision-workbench/plan.html", "docs/milestones/008-cli-decision-workbench/plan.md", "docs/milestones/008-cli-decision-workbench/proposals/closeout.md", "docs/milestones/008-cli-decision-workbench/proposals/perception-live-plugin-selection-amendment.md", "docs/milestones/008-cli-decision-workbench/proposals/perception-memory-workbench.md", "docs/milestones/008-cli-decision-workbench/proposals/perception-plugin-selection-amendment.md", "docs/milestones/008-cli-decision-workbench/proposals/perception-raw-capture-paused-refresh-amendment.md", "docs/milestones/008-cli-decision-workbench/proposals/replay-workbench-acceptance.md", "docs/milestones/completed.md", "lab/plugins/perception/classical_regions/plugin.json", "lab/plugins/perception/fastsam/plugin.json", "lab/plugins/perception/floor_continuity/plugin.json", "lab/plugins/perception/floor_continuity/src/plugin.py", "lab/plugins/perception/floor_continuity_capture/README.md", "lab/plugins/perception/floor_continuity_capture/__init__.py", "lab/plugins/perception/floor_continuity_capture/plugin.json", "lab/plugins/perception/floor_continuity_capture/src/__init__.py", "lab/plugins/perception/floor_continuity_capture/src/plugin.py", "tests/cli/test_workbench.py", "tests/lab/perception/test_floor_continuity_capture.py", "tests/milestones/test_replay_workbench_record_session.py"]
- `diff.included_changed_files`: ["cli/automa_cli/app.py", "cli/automa_cli/loopback_http.py", "cli/automa_cli/perception_view.py", "cli/automa_cli/workbench.html", "cli/automa_cli/workbench.py", "cli/automa_cli/workbench_contract.py", "cli/automa_cli/workbench_plugins.py", "cli/automa_cli/workbench_runner.py", "cli/automa_cli/workbench_server.py", "cli/automa_cli/workbench_source.py", "lab/plugins/perception/classical_regions/plugin.json", "lab/plugins/perception/fastsam/plugin.json", "lab/plugins/perception/floor_continuity/plugin.json", "lab/plugins/perception/floor_continuity/src/plugin.py", "lab/plugins/perception/floor_continuity_capture/README.md", "lab/plugins/perception/floor_continuity_capture/__init__.py", "lab/plugins/perception/floor_continuity_capture/plugin.json", "lab/plugins/perception/floor_continuity_capture/src/__init__.py", "lab/plugins/perception/floor_continuity_capture/src/plugin.py", "tests/cli/test_workbench.py", "tests/lab/perception/test_floor_continuity_capture.py", "tests/milestones/test_replay_workbench_record_session.py"]
- `diff.changed_directory_count`: 15
- `diff.added_lines`: 12828
- `diff.deleted_lines`: 67
- `diff.churn`: 12895
- `diff.included_added_lines`: 7788
- `diff.included_deleted_lines`: 67
- `diff.included_churn`: 7855
- `diff.decision_burden_delta`: 593
- `diff.new_import_edges`: ["cli/automa_cli/app.py->.workbench", "cli/automa_cli/loopback_http.py->__future__", "cli/automa_cli/loopback_http.py->http.server", "cli/automa_cli/loopback_http.py->json", "cli/automa_cli/loopback_http.py->socket", "cli/automa_cli/loopback_http.py->threading", "cli/automa_cli/loopback_http.py->typing", "cli/automa_cli/perception_view.py->.loopback_http", "cli/automa_cli/workbench.py->.perception_runs", "cli/automa_cli/workbench.py->.workbench_contract", "cli/automa_cli/workbench.py->.workbench_plugins", "cli/automa_cli/workbench.py->.workbench_runner", "cli/automa_cli/workbench.py->.workbench_server", "cli/automa_cli/workbench.py->.workbench_source", "cli/automa_cli/workbench.py->__future__", "cli/automa_cli/workbench.py->json", "cli/automa_cli/workbench.py->os", "cli/automa_cli/workbench.py->time", "cli/automa_cli/workbench.py->typing", "cli/automa_cli/workbench.py->webbrowser", "cli/automa_cli/workbench_contract.py->__future__", "cli/automa_cli/workbench_contract.py->typing", "cli/automa_cli/workbench_plugins.py->__future__", "cli/automa_cli/workbench_plugins.py->ast", "cli/automa_cli/workbench_plugins.py->autonomy.perception", "cli/automa_cli/workbench_plugins.py->autonomy.perception.activation", "cli/automa_cli/workbench_plugins.py->contextlib", "cli/automa_cli/workbench_plugins.py->dataclasses", "cli/automa_cli/workbench_plugins.py->hashlib", "cli/automa_cli/workbench_plugins.py->implementations.perception.catalog", "cli/automa_cli/workbench_plugins.py->json", "cli/automa_cli/workbench_plugins.py->os", "cli/automa_cli/workbench_plugins.py->pathlib", "cli/automa_cli/workbench_plugins.py->re", "cli/automa_cli/workbench_plugins.py->sys", "cli/automa_cli/workbench_plugins.py->typing", "cli/automa_cli/workbench_runner.py->.workbench_contract", "cli/automa_cli/workbench_runner.py->.workbench_plugins", "cli/automa_cli/workbench_runner.py->.workbench_source", "cli/automa_cli/workbench_runner.py->__future__", "cli/automa_cli/workbench_runner.py->autonomy.decision", "cli/automa_cli/workbench_runner.py->autonomy.decision.activation", "cli/automa_cli/workbench_runner.py->autonomy.perception", "cli/automa_cli/workbench_runner.py->autonomy.perception.activation", "cli/automa_cli/workbench_runner.py->autonomy.vehicle", "cli/automa_cli/workbench_runner.py->copy", "cli/automa_cli/workbench_runner.py->implementations.memory.catalog", "cli/automa_cli/workbench_runner.py->implementations.perception.catalog", "cli/automa_cli/workbench_runner.py->os", "cli/automa_cli/workbench_runner.py->pathlib", "cli/automa_cli/workbench_runner.py->threading", "cli/automa_cli/workbench_runner.py->time", "cli/automa_cli/workbench_runner.py->typing", "cli/automa_cli/workbench_runner.py->uuid", "cli/automa_cli/workbench_server.py->.loopback_http", "cli/automa_cli/workbench_server.py->.workbench_contract", "cli/automa_cli/workbench_server.py->.workbench_source", "cli/automa_cli/workbench_server.py->__future__", "cli/automa_cli/workbench_server.py->json", "cli/automa_cli/workbench_server.py->pathlib", "cli/automa_cli/workbench_server.py->threading", "cli/automa_cli/workbench_server.py->time", "cli/automa_cli/workbench_server.py->typing", "cli/automa_cli/workbench_server.py->urllib.parse", "cli/automa_cli/workbench_source.py->PIL", "cli/automa_cli/workbench_source.py->__future__", "cli/automa_cli/workbench_source.py->copy", "cli/automa_cli/workbench_source.py->dataclasses", "cli/automa_cli/workbench_source.py->hashlib", "cli/automa_cli/workbench_source.py->json", "cli/automa_cli/workbench_source.py->mimetypes", "cli/automa_cli/workbench_source.py->os", "cli/automa_cli/workbench_source.py->pathlib", "cli/automa_cli/workbench_source.py->re", "cli/automa_cli/workbench_source.py->typing", "lab/plugins/perception/floor_continuity_capture/src/plugin.py->__future__", "lab/plugins/perception/floor_continuity_capture/src/plugin.py->lab.plugins.perception.floor_continuity.src.plugin", "tests/cli/test_workbench.py->PIL", "tests/cli/test_workbench.py->__future__", "tests/cli/test_workbench.py->autonomy.decision.memory", "tests/cli/test_workbench.py->autonomy.perception", "tests/cli/test_workbench.py->cli.automa_cli.workbench", "tests/cli/test_workbench.py->json", "tests/cli/test_workbench.py->pathlib", "tests/cli/test_workbench.py->tempfile", "tests/cli/test_workbench.py->tests.support.cli_runner", "tests/cli/test_workbench.py->threading", "tests/cli/test_workbench.py->time", "tests/cli/test_workbench.py->unittest", "tests/cli/test_workbench.py->urllib.error", "tests/cli/test_workbench.py->urllib.parse", "tests/cli/test_workbench.py->urllib.request", "tests/lab/perception/test_floor_continuity_capture.py->__future__", "tests/lab/perception/test_floor_continuity_capture.py->autonomy.perception", "tests/lab/perception/test_floor_continuity_capture.py->autonomy.perception.mappers", "tests/lab/perception/test_floor_continuity_capture.py->autonomy.vehicle", "tests/lab/perception/test_floor_continuity_capture.py->json", "tests/lab/perception/test_floor_continuity_capture.py->lab.plugins.perception.floor_continuity.src.model", "tests/lab/perception/test_floor_continuity_capture.py->lab.plugins.perception.floor_continuity_capture.src.plugin", "tests/lab/perception/test_floor_continuity_capture.py->numpy", "tests/lab/perception/test_floor_continuity_capture.py->pathlib", "tests/lab/perception/test_floor_continuity_capture.py->tempfile", "tests/lab/perception/test_floor_continuity_capture.py->unittest", "tests/milestones/test_replay_workbench_record_session.py->__future__", "tests/milestones/test_replay_workbench_record_session.py->importlib.util", "tests/milestones/test_replay_workbench_record_session.py->io", "tests/milestones/test_replay_workbench_record_session.py->json", "tests/milestones/test_replay_workbench_record_session.py->pathlib", "tests/milestones/test_replay_workbench_record_session.py->sys", "tests/milestones/test_replay_workbench_record_session.py->tempfile", "tests/milestones/test_replay_workbench_record_session.py->unittest"]
- `diff.public_symbols_added`: ["cli/automa_cli/loopback_http.py:DEFAULT_CONTENT_SECURITY_POLICY", "cli/automa_cli/loopback_http.py:LOOPBACK_HOSTS", "cli/automa_cli/loopback_http.py:LoopbackHTTPRequestHandler", "cli/automa_cli/loopback_http.py:LoopbackHTTPServer", "cli/automa_cli/loopback_http.py:start_server_thread", "cli/automa_cli/loopback_http.py:stop_server_thread", "cli/automa_cli/loopback_http.py:validate_loopback_host", "cli/automa_cli/workbench.py:run_workbench_replay", "cli/automa_cli/workbench_contract.py:ReplayActionError", "cli/automa_cli/workbench_contract.py:WORKBENCH_ACTIONS", "cli/automa_cli/workbench_contract.py:WORKBENCH_ACTION_RESULT_SCHEMA", "cli/automa_cli/workbench_contract.py:WORKBENCH_DEFAULT_CADENCE_MS", "cli/automa_cli/workbench_contract.py:WORKBENCH_DEFAULT_LOOP", "cli/automa_cli/workbench_contract.py:WORKBENCH_DEFAULT_PACE", "cli/automa_cli/workbench_contract.py:WORKBENCH_ERROR_SCHEMA", "cli/automa_cli/workbench_contract.py:WORKBENCH_HOST", "cli/automa_cli/workbench_contract.py:WORKBENCH_MAX_ACTION_BYTES", "cli/automa_cli/workbench_contract.py:WORKBENCH_PACES", "cli/automa_cli/workbench_contract.py:WORKBENCH_SEQUENCE_ID", "cli/automa_cli/workbench_contract.py:WORKBENCH_SERVER_SCHEMA", "cli/automa_cli/workbench_contract.py:WORKBENCH_STATE_SCHEMA", "cli/automa_cli/workbench_plugins.py:DEFAULT_PLUGIN_ROOT_ID", "cli/automa_cli/workbench_plugins.py:PLUGIN_CATALOG_SCHEMA", "cli/automa_cli/workbench_plugins.py:PLUGIN_MANIFEST_SCHEMA", "cli/automa_cli/workbench_plugins.py:PluginCatalog", "cli/automa_cli/workbench_plugins.py:PluginCatalogError", "cli/automa_cli/workbench_plugins.py:PluginDescriptor", "cli/automa_cli/workbench_plugins.py:build_plugin_catalog", "cli/automa_cli/workbench_plugins.py:discover_plugin_catalog", "cli/automa_cli/workbench_plugins.py:packaged_plugin_catalog", "cli/automa_cli/workbench_runner.py:ImageReplayRunner", "cli/automa_cli/workbench_server.py:ReplayRunner", "cli/automa_cli/workbench_server.py:WORKBENCH_HTML_PATH", "cli/automa_cli/workbench_server.py:WorkbenchServer", "cli/automa_cli/workbench_source.py:ImageFeed", "cli/automa_cli/workbench_source.py:ReplayFrame", "cli/automa_cli/workbench_source.py:SourceValidationError", "cli/automa_cli/workbench_source.py:WORKBENCH_ADAPTER", "cli/automa_cli/workbench_source.py:WORKBENCH_DEFAULT_MAX_FRAMES", "cli/automa_cli/workbench_source.py:WORKBENCH_DEFAULT_MAX_IMAGE_BYTES", "cli/automa_cli/workbench_source.py:WORKBENCH_IMAGE_EXTENSIONS", "cli/automa_cli/workbench_source.py:WORKBENCH_UNSUPPORTED_IMAGE_EXTENSIONS", "cli/automa_cli/workbench_source.py:content_type_for_path", "cli/automa_cli/workbench_source.py:load_image_feed", "cli/automa_cli/workbench_source.py:normalize_image_directory", "lab/plugins/perception/floor_continuity_capture/src/plugin.py:CaptureFloorContinuityPlugin", "tests/cli/test_workbench.py:BlockingSecondMapper", "tests/cli/test_workbench.py:ErrorMemory", "tests/cli/test_workbench.py:ErrorStatusMapper", "tests/cli/test_workbench.py:FixtureMapper", "tests/cli/test_workbench.py:ImageReplayRunner", "tests/cli/test_workbench.py:WorkbenchTests", "tests/lab/perception/test_floor_continuity_capture.py:CaptureFloorContinuityCandidateTests", "tests/lab/perception/test_floor_continuity_capture.py:MANIFEST_PATH", "tests/lab/perception/test_floor_continuity_capture.py:PLUGIN_SPEC", "tests/milestones/test_replay_workbench_record_session.py:FakeWorkbench", "tests/milestones/test_replay_workbench_record_session.py:PromptScript", "tests/milestones/test_replay_workbench_record_session.py:RECORDER_PATH", "tests/milestones/test_replay_workbench_record_session.py:ROOT", "tests/milestones/test_replay_workbench_record_session.py:RecordSessionTests"]
- `diff.public_symbols_removed`: []
