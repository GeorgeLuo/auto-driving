# Milestone 008 — Perception-Memory Workbench Feasibility

| Field | Value |
| --- | --- |
| Status | closed |
| Milestone branch | `milestone/008-cli-decision-workbench` |
| Cumulative PR | [#167](https://github.com/GeorgeLuo/auto-driving/pull/167) (draft until whole-milestone closeout) |
| Current frontier | None (closed) |
| Started | 2026-08-26 |
| Action policy | Observation-only; no applied vehicle movement |

Shared planning contract: [README.md](../README.md) · [planning-contract.html](../planning-contract.html)

## Objective

Determine the smallest operator-useful and reusable perception-memory feature
slice that can be expressed through CLI-useful sequences, then deliver it in a
long-lived local workbench page. The CLI and workbench use the same
authoritative server-side sequence implementation and structured state; the
browser presents the feature without reimplementing its business logic. A
bounded audit of the relevant M007 sequences, current CLI capabilities, and
existing CLI-launched pages may select existing candidates or define a limited
number of new sequences when they are independently useful from both the CLI
and the workbench. The exact invocation boundary and visual form may be learned
through operator use. M008 closes with one usable slice and a durable
assessment of CLI, sequence, and signal gaps rather than broad scenario
coverage or a frontend framework migration.

## Completion Usage

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |
| Primary demonstration | The long-lived local workbench is open and the selected source, perception, and memory prerequisites are available | Provide or choose the supported input, use the declared replay controls, run or replay the authoritative sequence, follow state and metadata, inspect the result, and reset or start another run without closing the page | One recorded operator acceptance confirms that the perception-memory task is minimally useful; the page remains available for another run; useful progress, outcome, and next-action signals are visible without shell commands or raw JSON; the server uses the same sequence behavior as the CLI | M008-03, M008-04, M008-05, M008-06 |
| Recover a blocked run | A declared operator-reachable source, input, adapter, or sequence step fails | Inspect the failed boundary and named next action, make the external change or reset the isolated replay state, then retry from the persistent workbench | The selected slice's declared failure and recovery state remains visible; the workbench does not silently substitute another source, reconfigure a simulator, apply movement, or leave unintended replay state | M008-06 |

## Scope Boundaries

| In scope | Out of scope |
| --- | --- |
| A bounded audit of M007 perception-memory sequences, the current CLI feature set, and existing CLI-launched live and review pages | An exhaustive M007 sequence-family or CLI-leaf inventory, or treating every candidate as a required frontend feature |
| An authoritative sequence set for one useful feature slice, selected from existing behavior or completed with a limited number of independently CLI-useful sequences | A second-feature quota, a generic workflow builder, speculative frontend backlog, or feature count chosen for coverage alone |
| One long-lived local workbench page and server that remain available across runs and expose useful input, execution, result, recovery, and cleanup state | Treating per-run generated reports, raw stdout, JSON, logs, or command transcripts as the workbench product surface |
| One authoritative server-side sequence implementation and structured result contract shared by the CLI and workbench | Reimplementing sequence, perception, memory, decision, orchestration, or safety logic in browser code, or maintaining divergent CLI and workbench execution paths |
| Reuse, consolidation, or one-source adaptation of the loopback pages, publications, and presentation behavior used by the selected slice | Aligning every existing page or maintaining divergent CLI-page and workbench definitions that must be updated independently |
| The current lightweight local stack, including Python loopback serving and plain HTML, CSS, and JavaScript, evolved only as the selected slice requires | React adoption, a broad frontend-framework migration, a final design system, or a rewrite of every existing static evidence page |
| Operator-guided interface shapes and display granularity that can evolve while the selected feature purpose and signal meaning stay stable | Completing M006 cross-environment evidence, redesigning the external Metrics UI, remote hosting, authentication, packaging, or movement authority |
| Enabling or foundational work that a proposal ties directly to an existing exit criterion and one bounded review question, even when its exact mechanism is not named in this plan | Treating an unnamed mechanism as rejected by default, or expanding scope only because work may be useful later |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| M008-01 | A grounded, bounded assessment evaluates the relevant M007 perception-memory journeys, current CLI capabilities, and existing CLI-launched pages for operator usefulness, composability, required inputs and state, signal quality, CLI completeness, side effects, recovery, cleanup, and workbench fit | Met | PR #174 records the bounded assessment of the relevant M007 perception-memory candidates, current CLI capabilities, and existing loopback pages. |
| M008-02 | The assessment selects an authoritative sequence set for one useful slice; any newly defined sequence is limited, independently useful from the CLI, reusable in a larger feature, and explicit about inputs, signals, safety, recovery, and cleanup | Met | PR #174 selects workbench.image_replay.v1 as the one CLI-useful reusable composed journey with declared feed inputs, overlays, controls, safety, recovery, cleanup, and a bounded POC-completion envelope. |
| M008-03 | One long-lived local workbench page remains available across repeated runs while its server uses the same authoritative sequence implementation and structured run state as the CLI; the proposal may choose a CLI-process or shared application-API invocation boundary, the browser does not become a second sequence or business-logic authority, and delivery does not require React or a broad framework migration | Met | PR #191 records one loopback server identity and distinct run IDs across the first, second, failed, and recovered runs without relaunching the server, and cites the deterministic CLI/API runner contract and persistence coverage. |
| M008-04 | The existing CLI-launched pages and signal publications used by the selected slice are reused, consolidated, or given an explicit one-source adaptation boundary so the CLI page and workbench do not require divergent feature definitions | Met | PR #174 establishes the selected slice's one-source adaptation boundary between existing CLI-launched perception/memory pages and the workbench. |
| M008-05 | One selected perception-memory slice works end to end with a supported real input, meaningful tunable processing where the slice requires it, recognizable live and result signals, and one recorded affirmative operator acceptance of the primary demonstration's minimal usefulness and display granularity | Met | PR #191 records one affirmative operator acceptance of the image-replay primary demonstration at the delivered display granularity. |
| M008-06 | The implemented slice preserves its declared observation-only and side-effect boundaries for its named operator-reachable lifecycle cases, shows their failure and recovery state, avoids hidden simulator reconfiguration, and leaves no unintended worker or session mutation after cleanup | Met | PR #191 records declared source-failure recovery and cleanup with no worker, simulator, Metrics operation, movement, or recording. |
| M008-07 | The single bounded assessment used for M008-01 and M008-02 is maintained through closeout with durable CLI, sequence, signal, and page-surface gaps, distinguishing limitations worth a later product decision from observations that require no follow-up | Met | Closeout preserves the single M008 perception-memory assessment and records durable CLI, sequence, signal, page, source, plugin, transport, and visual-gap dispositions in PR #193 |
| M008-08 | Closeout confirms the selected sequence contract, one operator-accepted workbench slice, CLI and selected-page alignment, safe recovery and cleanup, and residual limits, then records whether another product decision is warranted without hiding unfinished work | Met | Closeout confirms the selected workbench.image_replay.v1 contract, accepted real image-replay slice, CLI/page alignment, observation-only recovery and cleanup, residual limits, and cumulative PR #167 topology in PR #193 |

## Current Delivery

### Current Frontier

**None**

- Reason: Milestone closed after PR #193.
- Revisit when: No in-milestone work remains.

### Next-Frontier Candidate

**None**

- Reason: No remaining work-order node is contracted.
- Revisit when: The next proposal may introduce a node.

### Frontier Map

- Path: none
- Cadence: linked-list

## Workflow History

| Frontier | State | Evidence |
| --- | --- | --- |
| M008 activation | ready_for_proposal | Activated from `main` after M007 closeout; M006 remains active on its own branch with draft cumulative PR #70 intentionally deferred, and M008 uses a separate branch and cumulative review surface. |
| Idle | idle | Plan revision: reframe M008 around evaluating M007 sequences as candidate workbench features, selecting a bounded useful set, and building interfaces that convey each selected feature's signals; the work order remains unchanged until the first proposal retargets it. |
| Idle | idle | Plan revision: bound M008 to one operator-useful perception-memory feasibility slice, a long-lived local workbench that reuses CLI-owned sequences and existing page signals, and a durable gap assessment; remove all-sequence and second-feature quotas, keep React out of scope, and leave the work order unchanged until proposal selection. |
| Idle | idle | Plan revision: tighten M008's finite acceptance boundary, limit alignment and recovery to the selected slice, permit one shared server-side CLI and workbench implementation, collapse repeated evidence obligations, remove superseded multi-feature decisions, and preserve frontier-discovered enabling work without changing the work order. |
| Perception-memory workbench journey | proposal_in_review | Started m008/perception-memory-workbench-proposal. |
| Perception-memory workbench journey | ready_for_implementation | Proposal PR #172 accepted at 09687f19acd61b286378fb65f3db915ce5e50d51 (reviewed head `09e82ae15158608247097c85b5e21a47b0a06511` by `GeorgeLuo` as `OWNER` at `2026-08-27T23:05:47Z`). |
| Perception-memory workbench journey | proposal_amendment_in_review | Started proposal amendment m008/amend-plugin-selection. |
| Perception-memory workbench journey | ready_for_implementation | Proposal amendment PR #179 accepted at 1189002447802442e857da8f5d9c2663ff85b86d (reviewed head `cb8ee7318c3820dc239def5343521396d9aab194` by `GeorgeLuo` as `OWNER` at `2026-09-01T23:17:14Z`). |
| Perception-memory workbench journey | proposal_amendment_in_review | Started proposal amendment m008/amend-live-plugin-selection. |
| Perception-memory workbench journey | ready_for_implementation | Proposal amendment PR #181 accepted at 5cf51585ac7951ea023a2a86fed786913daf626f (reviewed head `2b063feed81a050a3c24263dd453628d0e08cff3` by `GeorgeLuo` as `OWNER` at `2026-09-02T02:08:51Z`). |
| Perception-memory workbench journey | proposal_amendment_in_review | Started proposal amendment m008/amend-raw-capture-paused-refresh. |
| Perception-memory workbench journey | ready_for_implementation | Proposal amendment PR #189 accepted at b1e97ad8bd9c6ea7759bc7fa60331ec0a45d20d7 (reviewed head `5f07afdd1a09b066da79e4f886dc0448328f5e97` by `GeorgeLuo` as `OWNER` at `2026-09-03T04:22:22Z`). |
| Perception-memory workbench journey | implementation_in_review | Started m008/perception-memory-workbench. |
| Perception-memory workbench journey | accepted | Implementation PR #174 merged at 27b3c343de311e60219abc9b18b4ef293a28b445. |
| Replay workbench POC acceptance | proposal_in_review | Started m008/replay-workbench-acceptance-proposal. |
| Replay workbench POC acceptance | ready_for_implementation | Proposal PR #190 accepted at 8dca162ee776267091b1bf4ac23f188e19d471b6 (reviewed head `aa0b4ba65b55140ac80201c66ce3c22b105dd7dc` by `GeorgeLuo` as `OWNER` at `2026-09-03T21:56:31Z`). |
| Replay workbench POC acceptance | implementation_in_review | Started m008/replay-workbench-acceptance. |
| Replay workbench POC acceptance | accepted | Implementation PR #191 merged at 6c2f26a2ce34a5f38431e6b21d1269ea306f526d. |
| Milestone closeout | proposal_in_review | Started m008/closeout-proposal. |
| Milestone closeout | ready_for_implementation | Proposal PR #192 accepted at 05d3c3c9ee7cacc523295d7a54b73c5c29343450 (reviewed head `f546fcac1d38d1c27eda91b59d6402db0c7659e0` by `GeorgeLuo` as `OWNER` at `2026-09-04T04:47:27Z`). |
| Milestone closeout | implementation_in_review | Started m008/closeout. |
| Milestone closeout | accepted | Implementation PR #193 merged at 9d3fa1d1334e747656e5874dc19921a062616bce. |

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |
| #174 | Can an operator use a local workbench to replay a supported ordered image source through the existing perception-to-`Observation`-to-memory pipeline, inspect real capture overlays and memory effects, and control the bounded replay without shell commands, mock data, hidden simulator changes, or a second execution authority? | Accepted | M008-01, M008-02, M008-04 | PR #174 selects workbench.image_replay.v1 through the durable M008 assessment, composes the existing image-perception, Observation, and bounded-memory seams behind one shared server-side CLI/workbench runner, and documents the selected visual page adaptation boundary. |
| #191 | Can an operator use the implemented image-replay workbench to inspect real perception overlays and memory effects, control the declared replay, and affirm that this one local workflow is minimally useful at its delivered display granularity? | Accepted | M008-03, M008-05, M008-06 | PR #191 records one operator-accepted local image-replay workbench session: one loopback server identity stayed available across distinct first, second, failed, and recovered runs, overlays and memory were inspected from server state, declared failure/recovery/cleanup stayed observation-only, and the operator affirmed minimal usefulness at the delivered granularity. |
| #193 | Is milestone 008 complete as a whole—its selected image-replay contract, accepted workbench slice, CLI and page alignment, repeated-run recovery and cleanup, and durable residual assessment are all supported by accepted evidence, and the cumulative milestone is ready for whole-milestone review without hiding deferred product work? | Accepted | M008-07, M008-08 | M008 closeout judgment in closeout.md; completed.md M008 entry; single bounded assessment retained; accepted PR #174 and PR #191 evidence mapped; selected image-replay contract, operator acceptance, lifecycle safety, residual limits, and cumulative PR #167 preparation recorded in implementation PR #193 |

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |

## Milestone Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-08-26 | Open M008 before returning to M006 | M007 established a usable CLI product surface, and the next useful product question is whether those journeys can become a human-facing workbench |
| 2026-08-26 | Keep the engine, shared server-side sequence implementation, CLI, and workbench layered in that order | The CLI and workbench should expose the same behavior without creating a second decision authority |
| 2026-08-26 | Defer M006 without closing or duplicating it | M006-06 and M006-07 remain unmet on its own branch; M008 is an explicit parallel product milestone with separate scope and safety policy |
| 2026-08-26 | Keep open product issues as candidates until a selected M008 frontier needs one | Reclassifying #89, #90, #91, #93, #101, or #108 now would create issue churn without improving the first workbench decision |
| 2026-08-27 | Treat M007 sequences as candidate product features rather than command lists to display | M008 must test whether each relevant sequence has a coherent user goal and visual signals before investing in its interface |
| 2026-08-27 | Use one bounded assessment to select one perception-memory slice, permitting limited new sequences only when useful from both CLI and workbench | This keeps the milestone adaptable without requiring multiple assessments, features, interface proofs, or unrelated coverage |
| 2026-08-27 | Make one operator-accepted workbench slice and a durable gap assessment sufficient for M008 | A second-feature quota would create work beyond the current feasibility question; reuse is assessed in sequence and page boundaries rather than feature count |
| 2026-08-27 | Evolve a long-lived local page from the selected existing CLI-launched pages and signals, using one authoritative server-side sequence implementation | The workbench must stay aligned with the CLI and remain available across runs without requiring a CLI subprocess or duplicating business logic in the browser |
| 2026-08-27 | Keep React and broad frontend-framework migration out of M008 | The current Python loopback server and plain HTML, CSS, and JavaScript can prove the useful product slice before a framework decision is warranted |
| 2026-08-27 | Treat the first image-replay workbench as a POC with a bounded completion envelope and a queued operator-acceptance unit | Visual/interface refinement and narrowly necessary replay CLI support may evolve inside one journey; new adapters, semantics, authority, or operator goals require a later proposal rather than an endless review loop |

## Closeout

Blocked until every exit criterion is `Met`.

One evolving assessment is the shared evidence for M008-01, M008-02, and
M008-07. One integrated primary demonstration is the shared evidence for
M008-03 through M008-06. Closeout cites these artifacts and does not reproduce
them as new work.

Closeout will produce:

- `closeout.md`;
- a completed-milestone ledger entry;
- the bounded perception-memory assessment containing the selected
  authoritative sequence contract and durable gap disposition;
- one long-lived local workbench with an operator-accepted, human-usable
  perception-memory slice backed by the shared server-side implementation and
  structured signals, including its selected-page alignment, declared
  repeated-run lifecycle, failure, recovery, cleanup, and observation-only
  safety evidence;
- a residual-risk statement covering deferred candidates, M006 dependency,
  source-input limits, external Metrics UI capabilities, movement authority,
  transport limits, and visual design that remains intentionally adaptable.
