# M006 D2 implementation specification for Luna

This specification accompanies [live-decision-view.md](live-decision-view.md).
That proposal owns the contract, identities, schema, geometry rules, failure
matrix, D1 requests and workflow blocker. This file constrains implementation
and validation; it is not a separately accepted proposal or authorization to
change those decisions. Initial inspected base:
`a8c4483dea577afdb78094caa36d650f47882a27`.

## Assignment and limits

Implement on `m006/live-decision-view` in the existing isolated worktree only.
The operator has already authorized the combined A1 → L1 → A2 sequence and
local deterministic test servers. Do not wait for a separate proposal PR.
Root owns pushing/PR reconciliation and the pending decision-log entry unless
it explicitly delegates them. Do not change the primary checkout, other
worktrees, #202 or any live vehicle/simulator/device session.

Complete the design as written. Return a concrete incompatibility to A1 rather
than adding a transport, D1 producer, cross-frame transform, alternate schema
or workflow bypass. Unavailable physical source and host telemetry are valid
product states; they cannot be counted as successful live acceptance.

## Allowed files and components

| Path | Allowed change |
| --- | --- |
| `cli/automa_cli/decision.py` | Add info view discovery/probe and human output; extract/reuse a strict live read shared with stream if needed. Keep v0 exact key sets, acceptance semantics, apply/digest/record and error behavior compatible. Explicitly verify requested vehicle at the D2 read boundary. |
| `cli/automa_cli/decision_view.py` (new) | D2 view projection, generation/hash validation, immutable source association and bounded image cache, safe info probe helper. Avoid importing automation; keep imports acyclic. |
| `cli/automa_cli/perception_view.py` | Optional decision component, attach routes `/decision`, `/api/decision/latest`, `/api/decision/images/<image_id>`; pass captured bytes/metadata through existing ingress. Preserve perception/memory endpoints and their existing 8-frame behavior. |
| `cli/automa_cli/decision_view.html` | Actual polled decision page, current and retained source panes, supported geometry and distinct authority facts; no controls/actions |
| `cli/automa_cli/automation.py` | Attach component with immutable constructor activation/run/worker identity; feed existing capture and accepted cycle events; clear/invalidate on existing lifecycle paths. No change to capture/control/session policy or physical provider support. |
| `tests/cli/decision/test_live_decision_view.py` (new) | Public CLI/HTTP contract coverage in the map below |
| `tests/cli/decision/live_view_fixture.py` and `tests/cli/decision/fixtures/live_view/` (new, if needed) | Explicitly labeled deterministic producer/server fixture, finite scenarios, real disposable test process identity, source records and test images; no production fake-source mode |
| `tests/cli/decision/test_shadow_decision_surfaces.py`, `tests/cli/perception/test_view.py`, `tests/cli/memory/test_view_publication.py` | Necessary compatibility assertions only; preserve existing cases |
| Existing automation test modules under `tests/cli/automation/` | Add the narrow producer wiring/lifecycle assertion if the existing module owns it; identify exact changed path in the implementation receipt |
| `docs/reference/cli-simulator-perception-journey.md` | Small navigation/usage addition for the decision URL, local ownership, refusal semantics and D1 limitation |
| `docs/milestones/006-decision-facing-perception-readiness/evidence/live-decision-view/` | Sealed deterministic/browser result, source records/hashes, screenshots actually captured, adjacent derived HTML and reproducible command receipt |
| `docs/synthesis/artifacts/orchestration/2026-09-09-m006-d2-combined/` | Root-directed orchestration/review/repair receipts, retaining raw evidence and exact revisions |

No edits to `autonomy/`, decision policy/catalog/shadow adapter, Donkey runtime,
deployment/manage.py, physical observation integration, CLI parser/commands,
workflow.py, guidance, canonical contract, accepted proposals or #202 evidence.
Reuse `loopback_http.py`, image decoding and canonical JSON utilities unchanged;
no dependency addition. `plan.md`/`plan.html` are reserved to root's documented
exception reconciliation, not L1 product implementation. This proposal/spec
can receive A1-authored clarifications, never silent L1 contract rewrites.

## Route, schema and implementation constraints

The exact schema is the proposal's **Endpoint and data schema** section.
Implement these choices together:

1. Attach a D2 component to the already running `PerceptionViewServer` using
   the existing loopback origin and worker lifecycle. Existing `/` and `/memory`
   stay independent. An absent D2 component returns `producer_unavailable`.
2. Make `/decision?generation=<sha>` consume only the matching
   `/api/decision/latest?generation=<sha>`. Info probes the latter using a
   verified loopback origin, with redirects disabled and the declared budget,
   before exposing the former. Preserve `vehicle_decision_info_v0` and its
   existing `combined_view` fields; add only the specified view metadata.
3. Use the actual `latest_decision.json` and existing strict live acceptance,
   including current activation, running state, PID, summaries and time. Add
   owner/request vehicle and immutable startup activation-config comparisons
   at the D2 boundary. A server cache or old accepted decision is not a fallback
   after invalidation. Read consistent generation snapshots; retry at most once
   if state changes while assembling, then refuse. No unbounded polling in a
   request or producer critical section.
4. Publish exact captured bytes with capture identity; seal observation/source
   association from the corresponding accepted cycle. Use the existing
   capture-to-pending-context relationship. If stream publication succeeds but
   the image/source association is not ready, return `partial` until it is;
   never hide a race by using the newest camera image. Deep-copy immutable
   view inputs under a lock and serialize outside it. Do not hold locks across
   HTTP, file I/O or image decoding; producer errors remain nonfatal to cycles.
5. Keep a separate bounded store with the proposal's 64-image/32 MiB/8 MiB/
   16-million-pixel limits and 8 MiB source metadata bound. Pin current and
   memory-referenced source images, including stale. Pending capture entries
   count toward those limits. Release obsolete pins before admission under the
   newest accepted snapshot; HTTP requests acquire no lasting pins. Bound the
   decision-file read and full view JSON to 8 MiB; oversized view returns
   `view_payload_too_large`, never a truncated accepted cycle. No durable writes,
   arbitrary file resolver or remote image fetch. Per-image routes accept
   registered hash IDs only; refusal counters cannot grow an unbounded ID log.
6. Derive all source ref associations from the complete cycle source and the
   sealed archive. Compare record ID, provenance, evidence/observation/plugin
   IDs, original geometry and image generation. Use the reducer's exact plugin
   fallback and observation timestamp join specified in the proposal. Archive
   the needed capture/observation fields without evaluator siblings. Retained
   source frames get their own panes. Render valid bbox/polygon through
   source-normalized display scaling only; no zone-to-box invention or
   cross-frame projection. Strict-invalid publications reject in full before
   D2 projection; only accepted cycles can have partial component explanations.
7. Keep the original authority/host envelope available for inspection. The
   proposed typed host value is only a D2 consumer/fixture contract. A ready
   unknown generic envelope is not numeric physical telemetry. Never write a
   real host observation or copy authorized idle into one.
8. Implement one render transaction per accepted response with matched image
   loads. Show raw lifecycle/refs/contributions/full commands. Poll at 500 ms
   or slower, request timeout 2 s, expire using declared freshness, discard old
   replies (including superseded failures), and clear live content after
   rejection, disconnect or tab resume until refreshed. The 2 s transaction
   deadline includes image transfer/decode; use server remaining freshness
   minus monotonic elapsed transaction time and recheck before rendering.
   Use text nodes for raw labels/JSON; inline SVG coordinates
   come only from validated geometry. Keep unavailable reasons accessible as
   text, not color alone.

`decision_sha256` hashes unchanged accepted stream bytes canonically;
`generation_id` hashes full producer/activation identity; `image_id` hashes the
image descriptor identity and exact image SHA. Do not confuse them with replay
digests or a hardware authenticity assertion. No new fields go into
`STREAM_FRAME_EXACT_KEYS`, `CYCLE_EXACT_KEYS` or authority/plan schemas.

## Contract-to-test map

Use public `./cli/automa` subprocesses and actual loopback HTTP responses for
observable outcomes. Source fixtures may call existing producer/runner APIs to
arrange state, but expected results must come from declared records/cases, not
a second implementation of policy or the view projector. New test support
remains beside the decision tests and does not extend shared support with
domain logic. Never patch acceptance to make a subprocess producer look live.

| Test group | Proposal cases | Inputs / public observation |
| --- | --- | --- |
| `info_and_lifecycle` | D2-01, D2-04, D2-12 | Disposable staged runtime and real fixture-process PID/run; CLI info human/JSON, fetch returned URL/data, terminate owned fixture, repeat info. No activation retains existing error; valid activation without producer gives explicit unavailable. Physical observation alone never qualifies. |
| `current_and_retained` | D2-02, D2-03, D2-07 | Two distinct source images with known left/right geometry; real accepted cycles across fresh, retained, stale, inactive. Fetch image bytes and compare recorded SHA; inspect raw refs and association. At least 9 newer camera captures cannot evict pinned source; a 13th memory record must resolve. |
| `generation_and_freshness` | D2-04, D2-05 | Mutate one identity/timestamp/state/config field at a time via disposable publications; HTTP and CLI stream reject where shared contract applies. Test future, ceiling and ceiling+1 ms, reused frame ID, config-only restage, stopped/completed/dead worker and port reuse. |
| `source_integrity_and_bounds` | D2-06, D2-07 | Missing bytes/record/observation, duplicate ref target, plugin fallback and mismatch, provenance time/geometry conflict, conflicting bytes for same identity; remove original file after ingress. Verify strict-invalid publication gives 422 versus stream-valid source failure gives partial. Exercise count/byte/pixel/metadata/response caps, pending captures, pin exhaustion and admission after obsolete pins release. |
| `authority_separation` | D2-08 | Nonzero proposed steering + authorized idle + false flag; missing/generic/typed ready host reports; nonzero actual report; wrong-frame/run/time report. Assert raw cycle unchanged and derived host panel reflects only validated independent report. |
| `http_boundary` | D2-09 | GET/HEAD, invalid methods/query/hash, encoded traversal/URL input, unregistered image, external/redirecting discovery record, escaped raw labels. Validate statuses, no-store/CSP, bounded probe, and no arbitrary read/fetch. |
| `browser_transaction` | D2-02, D2-03, D2-05, D2-08, D2-10 | Actual browser on fixture URL, current/retained/stale/inactive screenshots, resize/letterbox, delayed/reordered JSON or image replies, failed image/network, expiry/tab return. Include image completion after expiry and an old failure after newer success. Compare visible fields, image content and drawn coordinates to recorded source. DOM/template text alone cannot pass visual acceptance. |
| Existing regressions | D2-11, D2-12 | Existing stage/info/stream/apply/replay/record, perception/memory views and automation startup/stop tests, followed by flagless deterministic suite |

Missing-image/geometry/host cases deliberately remain partial; this tests honest
behavior but cannot replace the positive current and retained spatial cases.
The local fixture is named `d2-fixture`, visibly labeled “deterministic fixture;
not Chase/Pi live evidence” in its receipt and source records. A real test PID
only proves the local test process. It is never named or presented as a Pi
producer. No runtime fixture switch or test-control HTTP endpoint ships in the
product.

## Exact validation commands

Run from this worktree. The new test module name below is fixed by this spec;
it does not exist at A1 and these are future L1 commands, not A1 results.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.cli.decision.test_live_decision_view -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.cli.decision.test_shadow_decision_surfaces \
  tests.cli.decision.test_commands \
  tests.cli.perception.test_view \
  tests.cli.memory.test_view_publication \
  tests.implementations.decision.test_shadow_adapter -v
env -u AUTOMA_TEST_LIVE_SIM -u AUTOMA_TEST_LIVE_PI \
  PYTHONDONTWRITEBYTECODE=1 python3 tests/run.py
PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py status \
  --plan docs/milestones/006-decision-facing-perception-readiness/plan.md
PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py validate \
  docs/milestones/006-decision-facing-perception-readiness/plan.md
PYTHONDONTWRITEBYTECODE=1 python3 docs/render_markdown.py --check
git diff --check
```

Only the flagless suite is authorized: no `--live-sim`, `--live-pi`, real
automation commands, discovery/device calls or canonical capture. If focused
test invocation needs environment configuration, record the exact addition and
its cause; do not hide a failed invocation.

The explicit environment removal above is required: existing live test classes
also inspect `AUTOMA_TEST_LIVE_SIM` / `AUTOMA_TEST_LIVE_PI` directly, so omitting
runner flags alone does not disable an inherited live-test setting. New focused
tests must remain deterministic regardless of those environment variables.

The fixture runner, if needed for browser inspection, must implement this
test-only invocation (not a new Automa command):

```sh
task_runtime_dir=$(mktemp -d /private/tmp/automa-d2-fixture.XXXXXX)
AUTOMA_RUNTIME_ROOT="$task_runtime_dir/vehicles" PYTHONDONTWRITEBYTECODE=1 \
  python3 tests/cli/decision/live_view_fixture.py \
  --runtime-root "$task_runtime_dir/vehicles" --vehicle-id d2-fixture \
  --scenario retained --port 0
```

The runner owns a real disposable process, stages only inside the supplied
temporary root using the public command, starts the actual existing view
server with D2 attached, and publishes controlled cycles/images through producer
interfaces. It prints its runtime root, real PID, info command and returned URL.
Require `--runtime-root`; refuse a nonempty preexisting vehicle runtime rather
than taking over state. Supported scenarios are `current-left`, `current-right`,
`retained`, `stale`, `inactive`, `missing-image`, `host-unavailable`,
`host-zero`, `host-nonzero`, `generation-mismatch`, `stopped`.
Refresh fixture publication time honestly while holding explicitly labeled
synthetic source/cycle inputs; never describe the fixture as a real lifecycle
capture. The deterministic lifecycle test uses a finite declared sequence.
Ctrl-C stops only this fixture and its listener; leave its evidence available
for inspection. Retention cleanup must not target any unrelated runtime.

In a second shell, use the exact printed temporary root, without substituting
the real runtime root:

```sh
AUTOMA_RUNTIME_ROOT=<printed-temporary-root> PYTHONDONTWRITEBYTECODE=1 \
  ./cli/automa vehicles info decision --id d2-fixture --json
AUTOMA_RUNTIME_ROOT=<printed-temporary-root> PYTHONDONTWRITEBYTECODE=1 \
  ./cli/automa vehicles info decision --id d2-fixture
AUTOMA_RUNTIME_ROOT=<printed-temporary-root> PYTHONDONTWRITEBYTECODE=1 \
  ./cli/automa vehicles stream decision --id d2-fixture --once --json
curl --fail-with-body --max-time 2 '<exact combined_view.api_url from info>'
```

Bracketed values above are output-dependent substitutions, not executable shell
defaults. Preserve the full actual commands and URLs in L1's receipt. Use the
returned `combined_view.url` in an actual browser and the same source records
for visual comparison. Repeat the fixture command with the named scenarios;
do not reconfigure a real vehicle. An HTTP 409/503 is expected in negative
scenarios, so a nonzero `curl --fail-with-body` exit is evidence of refusal,
not positive view acceptance.

For offline compatibility, the existing deterministic tests already stage a
disposable vehicle and cover these supported commands:

```sh
./cli/automa vehicles decision apply --id d2-fixture \
  --from-run <disposable-compatible-sequence> --json
./cli/automa vehicles decision apply --id d2-fixture \
  --from-run <disposable-compatible-sequence> --record --json
```

Execute only with `AUTOMA_RUNTIME_ROOT` and
`AUTOMA_DECISION_APPLY_OUTPUT_ROOT` set to disposable fixture directories and a
sequence whose vehicle ID matches. Do not run against an unrelated supplied
fixture ID and then weaken the identity check. Record actual canonical digest
equality and hashes; no host/generation parity claim is inferred from replay.

Root's actual PR validation command, after it supplies a final body and head:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/006-decision-facing-perception-readiness \
  --head-ref m006/live-decision-view \
  --base-sha a8c4483dea577afdb78094caa36d650f47882a27 \
  --head-sha <actual-D2-head> \
  --pr-body-file <actual-combined-PR-body-file>
```

Use the freshly observed exact target SHA if root later changes the integration
base; do not use the stale local milestone alias. On the current contract this
fails as documented in the proposal. No separate accepted-proposal SHA exists
for D2; do not fill the PR's Accepted Proposal section with #201 or A1's commit
as if it were an acceptance receipt. State the operator exception and blocker.
Retain required `Review Kind`, `Review Question`, `Validation` and
`Repair Cycle Ledger` sections with honest receipts. Never call
`complete-implementation` or `handoff` to clear this blocker.

## Browser acceptance and evidence receipt

| Inspection | Required visible evidence |
| --- | --- |
| Current left and right | Actual images, matching IDs/hashes, supported geometry in correct positions, full proposed command/selection and idle/false facts |
| Retained and stale | Different current/source images visibly labeled, overlay only on old source pane, original source refs/age, retained active versus stale command-null behavior |
| Inactive / missing / unsupported | No old selected overlay; empty refs or explicit component reason according to source |
| Host separation | Proposed, authorized and independent observed fields distinguishable; unavailable is not rendered as zero; fixture nonzero remains nonzero |
| Resize and delayed replies | Overlay stays tied to image content through aspect/viewport changes; reordered loads never combine a prior image with a newer overlay |
| Stop, mismatch, failed fetch and expiry | Live badge/content clears; visible refusal. Reusing old page URL against a new run cannot adopt it. |

Use the available browser skill/tool for actual inspection and screenshots in
L1. Browser runtime absence is an outstanding gate, not a template/HTTP test
pass. A1 has not used a browser or established browser availability.

Seal one bounded fixture result record and adjacent derived HTML in the
proposal's evidence directory, including code/base identity, commands, source
record/image hashes, case outcomes, screenshot paths where actually produced,
visual findings, and explicit fixture-only/live-unverified disposition. Source
records and images must be retained or reproducibly identified; screenshots
alone are insufficient. Root's orchestration archive links these records and
does not convert them into #202's canonical evidence.

## Constrained implementation sequence and handoff

1. Re-read active repository routing/state and this contract; record initial
   head and expected workflow failure. Keep the existing evidence current.
2. Add the focused view component and shared strict read wiring. Verify
   generation refusal and unchanged public stream behavior first.
3. Attach same-worker image/cycle ingress with bounded pinning and original
   source joins; expose immutable image and JSON routes. Exercise public
   identity/retention/geometry negative cases.
4. Wire info and the browser page. Exercise actual CLI discovery and HTTP, then
   visual positive and negative cases against explicitly controlled fixtures.
5. Run the focused regressions and flagless suite, docs and diff checks. Record
   failures and limitations exactly. Generate evidence HTML from actual result
   bytes when a sealed result is created.
6. Commit code/tests/docs with a clear message and hand root/A2 the exact head,
   changed paths, commands/results, D1 requirements still unavailable, browser
   disposition and workflow integration blocker. Root requests independent A2
   combined-result review; Luna does not self-accept the design or M006.

## A1 source and design-check receipt

Source packet supplied outside this repository, SHA-256 at A1 inspection:

| Input | SHA-256 |
| --- | --- |
| `agent-handoffs/m006-d2-combined/HANDOFF.md` | `333e976204e1ffb122faf1da0b071883c3a44e16066dbdc5cb1f1ffebf63099a` |
| `agent-handoffs/m006-d2-combined/D1-D2-RESOLUTION.md` | `9800a8af735ea1d3616c747365c71b88f3be567a64beb14cafeef425539f3808` |
| `agent-handoffs/m006-d2-combined/accepted-evidence-proposal.md` | `ba092296e39f25e1d740eee9198ecf5c2a10e4d33428a6e705ea5e8936f5e4ef` |

A1 read AGENTS, the agent surface, implementer/proposal/review-unit guidance,
the full canonical milestone contract, current plan, supplied packet and
relevant accepted decision-surface code/contracts. Read-only GitHub requests
confirmed #202 metadata and its plan at the pinned head, plus the target SHA.
No fetch, checkout, PR write, deployment or device/session operation was used.

At the inspected base, workflow status reported evidence
`ready_for_implementation`; standalone plan validation and
`docs/render_markdown.py --check` passed. In-memory exception/transition probes
produced the exact failures recorded in the proposal and wrote no files.
Proposal/template/link/diff checks apply to this A1 document delta; they are
not product or browser tests. Root should retain the final A1 commit and check
output in the orchestration archive without inventing observed model telemetry.

### A1 retry disposition

The retry preserved both uncommitted documents from the quota-interrupted A1
attempt and reviewed them against the same target, #202 metadata, source packet
hashes and current code. Bounded corrections specify strict-rejection precedence,
the reducer's plugin/timestamp attribution, missing response/status/bound details,
pending-cache/pin behavior, browser transaction expiry and inherited live-test
environment isolation. Serving ownership, decision policy, D1 exclusion and
the combined-job exception remain as declared above. Requested runtime is
`gpt-6-astra / xhigh`; an observed model/effort attestation is unavailable.

Retry document checks passed: proposal structure/template, five local links
and anchors, balanced fences/whitespace, all three packet hashes, accepted
evidence proposal byte equality with the branch artifact, existing future test
module paths, live-test environment exclusion, standalone plan validation,
generated-document check and the two-path scope audit. The plan, plan HTML and
`workflow.py` remain byte-equal to the initial base. No product tests ran.

Reproduce the read-only proposal/template check from the worktree:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY'
from pathlib import Path
from docs.milestones import workflow
proposal = Path('docs/milestones/006-decision-facing-perception-readiness/proposals/live-decision-view.md').read_text()
workflow.validate_proposal_text(proposal, review_kind='Behavioral feature slice')
template = workflow.load_handoff_template(proposal)
assert template['criterion_updates'] == {}
assert template['risk_remove'] == [] and template['risk_upsert'] == []
assert template['next_frontier']['state'] == 'none'
print('Proposal structure and handoff template: PASS (not workflow acceptance)')
PY
```

The standalone template parses, but standard simulation against the unchanged
plan correctly refuses with `Expected Handoff validation requires
proposal_in_review`. The in-memory log-only candidate passes standalone plan
validation and still fails review-unit validation with `review-unit PR must
append exactly one workflow-history transition`. The evidence-transition and
current-replacement probes also reproduce the proposal's exact refusals. No
candidate plan was written and no handoff was applied.

For the committed A1 head, the following read-only command uses a local
diagnostic body on stdin; it is not a PR creation or a claim to validate an
existing combined PR body. Expected exit is 2 with:

```text
Milestone workflow error: review-unit PR must append exactly one workflow-history transition
```

```sh
PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/006-decision-facing-perception-readiness \
  --head-ref m006/live-decision-view \
  --base-sha a8c4483dea577afdb78094caa36d650f47882a27 \
  --head-sha HEAD --pr-body-file /dev/stdin <<'D2_BODY'
# D2 combined job: A1 design diagnostic

## Milestone Context

M006; operator-authorized combined D2 branch; #202 retains evidence ownership.

## Accepted Proposal

No separate D2 acceptance; A1 contract and constrained spec precede L1/A2.

## Review Kind

Behavioral feature slice

## Review Question

Does decision info expose a correlated current/retained source view?

## Validation

A1 documentation checks only; workflow integration remains blocked.

## Repair Cycle Ledger

| Cycle | Review receipt | Classification | Highest severity | Repair revision | Contract impact |
| --- | --- | --- | --- | --- | --- |
| None | None | None | None | None | None |
D2_BODY
```

Record the resolved `git rev-parse HEAD` beside that command's result. R0/R1
still owns the pending canonical decision-log entry, workflow accommodation,
source-packet archive and orchestration receipts. A1 authors only these two
documents and commits them; no product test, browser inspection, live session,
plan change, PR write or criterion promotion is included in this retry.
