## Prospective README appendix: usage sequences and human confirmation

This is the consolidated source for a section to add to the evidence README near the end of this PR. It summarizes the sequence discussion above; it is **proposed, not executed**, and does not change #88's M007-05 acceptance gates or verdict.

The earlier JSON-heavy sequence sketches should not be copied verbatim. The durable version should use the human-facing CLI and launched browser as the primary operator surfaces. JSON and recorded artifacts are supporting evidence only.

### Confirmation standard

Every tracked sequence should record:

- the operator question and prerequisites;
- exact human-first commands;
- safety class and side effects;
- **one primary confirmation** that is either a concise CLI verdict or a launched visual state;
- an optional secondary visual/CLI cue;
- optional JSON or recorded evidence;
- unconditional cleanup;
- known limits, discrepancies, and linked issues; and
- status: `proposed`, `ready to try`, `partial capability`, `blocked`, `passed`, or `finding`.

Operator-facing rules:

1. Do not make a person parse JSON to decide whether a sequence worked.
2. Every live automation start used for visual inspection includes `--open-view`.
3. The Automa view is the primary live perception surface. Its **Memory map** link is the primary live evidence-memory surface.
4. A record path, digest, or raw payload is never the sole human success signal.
5. Comparison workflows need one compact table or one consolidated visual review; otherwise mark them partial.
6. The sequence names the exact cue before it is run—for example `Ready for:`, `PASS/FAIL`, `Deterministic: yes`, visible plugin presence, overlay freshness, or keys/epoch before → after.
7. Cleanup and restored staged configuration are part of the visible outcome.

### Candidate catalog

| ID | Operator outcome | Primary human confirmation | Visual surface | Current status |
| --- | --- | --- | --- | --- |
| US-01 | Discover the bounded Chase journey from public help | The next leaf and required flags are visible without implementation knowledge | None required | Ready to try in #88 |
| US-02 | Reach a healthy passive camera/perception view and cleanly stop | CLI prints `Ready for: inspect perception and stop automation`; final status reports stopped | Automatically opened Automa view shows `Live | overlay current` and a nonblank correlated frame | #88 acceptance sequence |
| US-03 | Capture once and compare perception implementations on the same frames | Compact human experiment summary / candidate comparison table | Recorded per-run `review.html`; not auto-opened or consolidated today | Partial; #89 and #90 |
| US-04 | Swap a live algorithm/plugin configuration in the same simulator environment | Restart reaches `Ready for:`; plugin list matches the staged configuration | Automatically opened view visibly shows plugin runs, findings, overlays, and memory-key count | Partial; manual rollback today, #91 |
| US-05 | Confirm real perception becomes bounded, attributable memory | Final `Memory check: chase-sim-chaser PASS/FAIL` | Open view → **Memory map** shows live health, key count, epoch, bounds, and retained keys | Ready to try |
| US-06 | Ablate motion tracking and observe downstream memory differences | Both trials reach readiness and the lifecycle check gives a concise verdict | Plugin Runs shows `motion-tracks` present/absent; Memory map shows resulting key/provenance changes | Ready with caveat: live frames differ |
| US-07 | Observe whether slow perception makes temporal memory ineffective | Human automation status exposes `captured`, `processed`, and `skipped` on one run line | View shows overlay current/stale state and perception duration | Partial: no synthesized overload verdict |
| US-08 | Recover from suspicious retained evidence | Reset output shows `Keys: N -> 0` and `Epoch: old -> new` | Memory map shows the new epoch and subsequent repopulation | Ready to try |
| US-09 | Freeze and reproduce a live memory anomaly | Replay prints `Deterministic: yes (two independent passes matched)` | Recorded provenance extract is optional investigation evidence | Ready to try |
| US-10 | Qualify a candidate on labeled physical-check frames | First line says `promote_candidate` or `reject_keep_control` | Recorded summary/contact-sheet evidence is secondary | Ready when physical labeled input exists |

### US-01 — Help to runnable leaf

**Question:** Can an operator discover the supported passive workflow without knowing the parser or runtime layout?

```sh
./cli/automa help
./cli/automa vehicles help
./cli/automa vehicles automation help
./cli/automa vehicles automation run --help
```

**Primary confirmation:** the operator can identify `status`, `update perception`, `automation run --observe-only --frames 0 --open-view`, `automation stop`, and the opt-in meaning of `--record`.

This is a discoverability judgment, so no browser view is required.

### US-02 — Passive Chase perception journey

**Question:** Can an operator attach to the existing Chase session, see current perception, and leave no worker running without changing simulator state?

```sh
./cli/automa vehicles status --chase-url http://localhost:5050

./cli/automa vehicles update perception \
  --id chase-sim-chaser \
  --algorithm lightweight_observer

./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --frames 0 \
  --open-view

./cli/automa vehicles status --id chase-sim-chaser
./cli/automa vehicles automation stop --id chase-sim-chaser
./cli/automa vehicles status --id chase-sim-chaser
```

**Primary confirmation:** startup prints `Ready for: inspect perception and stop automation`; the launched page shows a nonblank current frame and `Live | overlay current`.

**Cleanup confirmation:** the final human status reports the worker stopped and current-generation view unavailable.

This remains the only sequence that determines #88's M007-05 verdict.

### US-03 — Capture once, run many perception implementations

**Question:** Which packaged algorithm or ready candidate produces the most useful representation on the exact same captured frames?

```sh
./cli/automa vehicles update perception \
  --id chase-sim-chaser \
  --algorithm lightweight_observer

./cli/automa vehicles perception run \
  --id chase-sim-chaser \
  --frames 60 \
  --interval-s 0.25 \
  --record

SRC=<recorded-run-directory>

./cli/automa vehicles perception apply "$SRC" --algorithm lightweight_observer
./cli/automa vehicles perception apply "$SRC" --algorithm sim_debug
./cli/automa vehicles perception apply "$SRC" --algorithm visual_observer

./cli/automa vehicles perception compare "$SRC" --record
```

**Primary confirmation today:** human apply summaries and the compact candidate comparison table show failed frames, evidence kinds, representation health, latency, and RSS.

**Visual evidence today:** each recorded run has a `review.html`, but it is not launched automatically and packaged algorithms are not consolidated in one review. Therefore this sequence remains partial rather than asking the operator to compare multiple raw reports.

**Limits:**

- `perception compare` includes ready lab candidates, not packaged algorithms.
- Live capture runs the baseline mapper inline, so acquisition cadence is not mapper-neutral.
- Avoid rapid `perception apply --record` loops until [#89](https://github.com/GeorgeLuo/auto-driving/issues/89) is fixed; same-second runs can collide.
- [#90](https://github.com/GeorgeLuo/auto-driving/issues/90) owns the unified exact-frame experiment and review surface.

Representation health is structural stability, not semantic accuracy.

### US-04 — Live algorithm/plugin trial

**Question:** What visibly changes when an operator swaps perception configuration while preserving the same running simulator environment?

```sh
./cli/automa vehicles automation stop --id chase-sim-chaser

./cli/automa vehicles update perception \
  --id chase-sim-chaser \
  --algorithm visual_observer

./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --frames 0 \
  --open-view

# Inspect, then stop before changing activation.
./cli/automa vehicles automation stop --id chase-sim-chaser

./cli/automa vehicles perception disable motion_tracks \
  --id chase-sim-chaser

./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --frames 0 \
  --open-view
```

**Primary confirmation:** each startup reaches `Ready for:`; Plugin Runs visibly includes or excludes `motion-tracks`, and overlays/findings change accordingly.

**Required cleanup:**

```sh
./cli/automa vehicles automation stop --id chase-sim-chaser
./cli/automa vehicles perception enable motion_tracks --id chase-sim-chaser
```

The worker loads perception only at startup: changing `enable/disable` while it is running does not hot-reload the mapper. The two trials use different live frames and fresh process-local memory, so this supports visual intuition and lifecycle testing—not a fair semantic A/B claim. [#91](https://github.com/GeorgeLuo/auto-driving/issues/91) owns transactional restart/verification/rollback.

### US-05 — Ordinary perception-to-memory lifecycle

**Question:** Does selected perception become attributable retained evidence, survive brief dropout, expire, and reset without movement?

```sh
./cli/automa vehicles update perception \
  --id chase-sim-chaser \
  --algorithm visual_observer

./cli/automa vehicles update memory \
  --id chase-sim-chaser \
  --implementation bounded_evidence

./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --frames 0 \
  --open-view

./cli/automa vehicles stream memory \
  --id chase-sim-chaser \
  --once

./cli/automa vehicles memory check \
  --id chase-sim-chaser \
  --record
```

**Primary confirmation:** the final CLI block says `Memory check: chase-sim-chaser PASS` or `FAIL`.

**Visual confirmation:** the launched perception view links to **Memory map**, whose status shows `Live | N keys` or `Live | empty map`; the map exposes epoch, bounds, implementation, record keys, values, and source plugins.

The lifecycle check intentionally resets live memory. Its recorded provenance extract is supporting evidence, not the primary verdict.

**Cleanup:**

```sh
./cli/automa vehicles automation stop --id chase-sim-chaser
```

### US-06 — Motion-tracking ablation with memory

**Question:** Does motion tracking add useful temporal evidence, or mainly add record churn and capacity pressure?

Use US-05 as the `visual_observer` baseline. Then stop, disable `motion_tracks`, restart with `--open-view`, and repeat the human `memory check --record`.

**Primary confirmation:** both trials give a concise lifecycle verdict.

**Visual confirmation:** Plugin Runs proves whether `motion-tracks` ran; Memory map makes resulting retained keys and source-plugin attribution inspectable without reading JSON.

Compare only observable structure: key count, provenance/source plugin, conflict count, capacity eviction count, retention/expiry behavior, and obvious visual findings. Do not claim semantic superiority because frames differ. Each restart creates a fresh memory boundary, preventing old evidence from contaminating the alternate configuration.

Restore `motion_tracks` after cleanup.

### US-07 — Perception backpressure and temporal memory

**Question:** Can an expensive observer keep overlays and memory useful at the requested capture cadence?

```sh
./cli/automa vehicles automation run \
  --id chase-sim-chaser \
  --observe-only \
  --frames 0 \
  --interval-s 0.02 \
  --open-view

./cli/automa vehicles automation status --id chase-sim-chaser
./cli/automa vehicles stream memory --id chase-sim-chaser --once

# Repeat the two human inspections after an operator-chosen observation interval.

./cli/automa vehicles automation stop --id chase-sim-chaser
./cli/automa vehicles automation status --id chase-sim-chaser
```

**Primary confirmation today:** the human automation `run:` line exposes `captured`, `processed`, and `skipped`; a rapidly increasing skipped count is the direct backpressure signal.

**Visual confirmation:** the launched view reports overlay current/stale state and perception duration; Memory map reports health, key count, bounds, and last visible state.

This remains partial because neither CLI nor frontend currently synthesizes those values into an `overloaded` verdict or threshold. Actual usage should establish whether that missing summary deserves a feature request.

### US-08 — Clear suspicious retained evidence

**Question:** Can an operator clear suspect memory and observe current perception repopulate a new epoch?

While US-05 automation remains running:

```sh
./cli/automa vehicles stream memory --id chase-sim-chaser --once
./cli/automa vehicles memory reset --id chase-sim-chaser
./cli/automa vehicles stream memory --id chase-sim-chaser --once
```

**Primary confirmation:** reset output visibly reports `Keys: N -> 0` and `Epoch: old -> new`. Failure to confirm the empty epoch returns nonzero and prints a warning.

**Visual confirmation:** Memory map shows the new epoch and subsequently repopulated keys. Only post-boundary evidence may appear; old retained keys must not reappear as history.

### US-09 — Freeze and replay an anomaly

**Question:** Can an unexpected live memory outcome be reproduced deterministically offline?

Take the `record_dir` printed by US-05:

```sh
CHECK_DIR=<record-directory-from-memory-check>

./cli/automa vehicles memory replay "$CHECK_DIR" \
  --id chase-sim-chaser \
  --implementation bounded_evidence \
  --record
```

**Primary confirmation:** human output says `Deterministic: yes (two independent passes matched)`. A mismatch is an explicit non-deterministic failure with both digests.

**Secondary evidence:** the recorded provenance extract maps retained keys to source observations/frames. It does not need to be opened to establish determinism.

### US-10 — Physical labeled qualification

**Question:** Does a ready candidate materially improve labeled physical-check behavior without unacceptable regressions?

```sh
./cli/automa vehicles perception check \
  --id <picar-id> \
  --record

./cli/automa vehicles perception qualify \
  --from-check-run <recorded-check-run> \
  --candidate <ready-candidate>
```

**Primary confirmation:** the first qualification line is `Physical strategy qualification: promote_candidate` or `reject_keep_control`, followed by the rationale.

**Secondary evidence:** the recorded summary and source frames support review. Qualification is valid only for a labeled physical-check run and does not prove onboard Pi viability.

### Cross-cutting limits and issue routing

- [#89](https://github.com/GeorgeLuo/auto-driving/issues/89): recorded perception apply identity collisions.
- [#90](https://github.com/GeorgeLuo/auto-driving/issues/90): exact same-frame packaged/candidate/plugin experiment matrices with a unified review.
- [#91](https://github.com/GeorgeLuo/auto-driving/issues/91): transactional live perception trials with proven activation identity and rollback.
- Live `enable/disable` requires worker stop/start before its effect is observable.
- Restarting creates fresh process-local memory; continuity across restarts is not claimed.
- Same-environment live trials are useful for intuition and lifecycle stress but are not frame-identical comparisons.
- Human output and automatically launched visuals are primary; `--json` remains available for later machine evidence.
- Every live scenario remains observation-only and ends with explicit worker cleanup.

### Intended README treatment

Near the end of #88, copy this into a Markdown appendix titled **Follow-on usage scenario candidates**. Keep every entry `proposed — not executed` unless that exact scenario has actually been run and evidenced. Do not add the candidates to `result.json`, the M007-05 machine gates, or the #88 verdict.

Review in #88 should verify that the commands exist and that each named human/visual confirmation matches the current product surface. Later work validates actual scenario outcomes; it should not require a separate PR merely to re-approve this document's wording.
