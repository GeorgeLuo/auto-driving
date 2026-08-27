# Milestone 008 — M007 Scenario Workbench

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `milestone/008-cli-decision-workbench` |
| Cumulative PR | [#167](https://github.com/GeorgeLuo/auto-driving/pull/167) (draft until whole-milestone closeout) |
| Current frontier | None (idle; first proposal not started) |
| Started | 2026-08-26 |
| Action policy | Observation-only; no applied vehicle movement |

Shared planning contract: [README.md](../README.md) · [planning-contract.html](../planning-contract.html)

## Objective

Establish which M007 CLI sequences function as useful operator features when
presented in a workbench, then build the visual interfaces that make those
features usable. The workbench lets a person recognize a feature's purpose,
provide its inputs, follow its progress, interpret its meaningful signals and
outcome, and recover or clean up without translating raw commands or runtime
topology. The exact visual forms may be learned through use, while the core
engine and CLI remain the execution authority and the milestone still closes
with a bounded set of real, human-usable workbench features.

## Completion Usage

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |
| Primary demonstration | One M007 sequence selected by the baseline has its required local environment available | Open the workbench, choose the feature, provide its inputs, run it, interpret its task-specific visual signals and result, then recover or clean up as needed | The sequence functions as a coherent feature rather than a displayed command list: a person can understand what happened and what to do next without using the shell, while the real CLI path executes safely underneath | M008-03, M008-04, M008-05, M008-07 |
| Evaluate M007 feature candidates | Accepted M007 sequence registry, catalogs, and evidence | Examine the sequence families through their user goal, required inputs, side effects, state transitions, outputs, human signals, recovery, cleanup, and visual needs | A bounded baseline states which sequences can become useful workbench features now, what interface each needs, and why the remaining sequences are not selected yet | M008-01, M008-02 |
| Use another workbench feature | A meaningfully different M007 sequence selected by the baseline is available | Choose and run it through the same workbench | The common workbench still handles discovery, execution state, recovery, and cleanup while a task-appropriate interface conveys this feature's distinct signals | M008-03, M008-04, M008-06, M008-07 |
| Recover a blocked feature | A prerequisite, external capability, CLI command, or worker is unavailable | Inspect the failed step and follow the workbench recovery, or make the named external change, then retry | The failed boundary and next action remain visible; the workbench does not silently reconfigure the simulator, apply movement, or leave a worker running | M008-07 |

## Scope Boundaries

| In scope | Out of scope |
| --- | --- |
| A baseline evaluation of M007 sequence families as candidate workbench features, grounded in their accepted catalogs and evidence | Treating every CLI leaf or M007 candidate as a required frontend feature |
| A bounded set selected from that baseline and implemented as real features in a local human-facing workbench | A generic workflow builder, speculative frontend backlog, or feature count chosen for coverage alone |
| A common workbench for feature discovery, inputs, execution state, recovery, and cleanup, with task-appropriate interfaces for each selected feature's signals | Flattening every feature into raw stdout, JSON, logs, or one generic visualization |
| Real CLI execution and machine-readable results beneath the interface, with the core engine and CLI retaining authority | Reimplementing perception, memory, decision, orchestration, or safety logic in the frontend |
| Interface shapes that can evolve as the baseline and hands-on use reveal what people need to see | Freezing a final design system, animation set, or layout before the features teach what is useful |
| Explicit dependency and unavailable states where a selected feature needs behavior not present on the M008 base | Completing M006 cross-environment evidence, changing its decision policy, or copying unfinished M006 work into M008 |
| A maintainable local UI under this repository's frontend surface | Redesigning the external Metrics UI, remote or public hosting, authentication, desktop packaging, or movement authority |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| M008-01 | A grounded baseline evaluates the accepted M007 sequence families as candidate workbench features by recording each relevant user goal, required inputs and environment, side effects, state transitions, outputs, human-recognizable signals, recovery and cleanup, and visual-interface needs | Unmet | M007 provides sequence definitions and evidence, but their workbench feature value has not been evaluated |
| M008-02 | The baseline selects a bounded implementation set based on user usefulness and interface feasibility, and explicitly records why other M007 sequences are deferred or unsuitable without turning them into a backlog | Unmet | Selection follows baseline evidence rather than a predetermined sequence count or coverage target |
| M008-03 | One local workbench surface discovers and launches the selected features through their real CLI sequences, presents feature inputs and execution state, and does not create a second core-engine or CLI authority | Unmet | The shared workbench boundary has not yet been proposed or implemented |
| M008-04 | Each implemented feature has a usable visual interface that conveys its task-specific signals, state, and outcome without requiring raw command transcripts, JSON, runtime files, or internal topology as the primary explanation | Unmet | Exact visual forms remain intentionally open until the baseline and hands-on use establish them |
| M008-05 | One baseline-selected M007 sequence works end to end as a human-usable workbench feature with real inputs, execution, recognizable success or failure, recovery, and cleanup | Unmet | The baseline must choose the primary feature before implementation is contracted |
| M008-06 | At least one meaningfully different baseline-selected M007 sequence works through the same workbench while retaining the interface needed for its distinct signals, proving the product can grow without an engine or CLI fork | Unmet | A second feature is required to distinguish a reusable workbench from a one-off visual wrapper |
| M008-07 | Every implemented feature preserves its declared safety and side-effect boundaries, reports unavailable dependencies and exact recovery, avoids hidden simulator reconfiguration, and leaves no unintended worker or session mutation after cleanup | Unmet | Safety, failure, and cleanup proof must accompany each selected feature |
| M008-08 | Closeout confirms the M007 feature baseline, bounded feature selection, usable workbench interfaces, real CLI parity, safe recovery and cleanup, and residual limits, then records the next product decision without hiding unfinished work | Unmet | Milestone closeout is selected only after the implementation criteria are Met |

## Current Delivery

### Current Frontier

**None**

- Reason: M008 is open with its first bounded work-order node queued; no proposal review has started.
- Revisit when: The operator is ready to review the first workbench journey proposal.

### Next-Frontier Candidate

**Perception-memory workbench journey**

- Proposal branch: `m008/perception-memory-workbench-proposal`
- Implementation branch: `m008/perception-memory-workbench`
- Proposal path: `docs/milestones/008-cli-decision-workbench/proposals/perception-memory-workbench.md`
- Review kind: Behavioral feature slice
- Review question: Can an operator run the accepted M007 perception-to-memory lifecycle sequence from a local workbench and inspect the real CLI lifecycle, results, and cleanup without shell commands, mock data, hidden simulator changes, or a second execution authority?
- Acceptance owner: Workbench scenario runner and CLI machine-readable boundary
- Exit criteria affected: M008-01, M008-02, M008-04
- Prerequisite: The accepted M007 continuity catalog and CLI sequence runner are available on `main`, and a supported local observation-only run can be prepared.
- Milestone-level non-goal: Full M006 shadow-decision evidence, all M007 scenarios, engine or CLI redesign, Metrics UI redesign, movement, or remote hosting.

### Frontier Map

- Path: `Perception-memory workbench journey`
- Cadence: linked-list

#### Node: Perception-memory workbench journey

- Proposal branch: `m008/perception-memory-workbench-proposal`
- Implementation branch: `m008/perception-memory-workbench`
- Proposal path: `docs/milestones/008-cli-decision-workbench/proposals/perception-memory-workbench.md`
- Review kind: Behavioral feature slice
- Review question: Can an operator run the accepted M007 perception-to-memory lifecycle sequence from a local workbench and inspect the real CLI lifecycle, results, and cleanup without shell commands, mock data, hidden simulator changes, or a second execution authority?
- Acceptance owner: Workbench scenario runner and CLI machine-readable boundary
- Exit criteria affected: M008-01, M008-02, M008-04
- Prerequisite: The accepted M007 continuity catalog and CLI sequence runner are available on `main`, and a supported local observation-only run can be prepared.
- Non-goals: Full M006 shadow-decision evidence, all M007 scenarios, engine or CLI redesign, Metrics UI redesign, movement, or remote hosting.

## Workflow History

| Frontier | State | Evidence |
| --- | --- | --- |
| M008 activation | ready_for_proposal | Activated from `main` after M007 closeout; M006 remains active on its own branch with draft cumulative PR #70 intentionally deferred, and M008 uses a separate branch and cumulative review surface. |
| Idle | idle | Plan revision: reframe M008 around evaluating M007 sequences as candidate workbench features, selecting a bounded useful set, and building interfaces that convey each selected feature's signals; the work order remains unchanged until the first proposal retargets it. |

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| M007 sequences were accepted as CLI journeys, not proven as workbench features | A sequence may be technically runnable yet have weak user value or signals that do not support a usable interface | Use the baseline to evaluate feature purpose and visual proof before selecting implementation work |
| Different M007 features may need materially different visual explanations | A prematurely generic interface could hide the signals that make each feature useful | Keep discovery and execution common while allowing task-specific views inside the same workbench |
| The smallest reliable CLI-to-workbench transport is not yet chosen | A premature service or protocol could create work before real feature needs are understood | Let the first implementation proposal choose the smallest transport that preserves real CLI behavior and machine-readable results |
| The full M006 shadow decision surfaces are accepted on the M006 branch but are not in `main` | A baseline candidate that needs the full shadow proposal lifecycle cannot be implemented from the current M008 base | Keep M006 ownership separate and mark the candidate unavailable or deferred until its CLI contract is available; do not duplicate it in M008 |
| Human-friendly presentation may change as operators use selected features | Freezing a visual layout would turn useful feedback into rework | Keep feature purpose, signals, and outcome stable while allowing the interface form to adapt |

## Milestone Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-08-26 | Open M008 before returning to M006 | M007 established a usable CLI product surface, and the next useful product question is whether those journeys can become a human-facing workbench |
| 2026-08-26 | Keep the engine, CLI, and workbench layered in that order | The workbench should make the CLI understandable and usable without creating a second decision authority |
| 2026-08-26 | Start with one accepted M007 scenario and prove a second through the same mechanism later | A real vertical slice establishes feasibility and scaling evidence without creating a speculative frontend backlog |
| 2026-08-26 | Defer M006 without closing or duplicating it | M006-06 and M006-07 remain unmet on its own branch; M008 is an explicit parallel product milestone with separate scope and safety policy |
| 2026-08-26 | Keep open product issues as candidates until a selected M008 frontier needs one | Reclassifying #89, #90, #91, #93, #101, or #108 now would create issue churn without improving the first workbench decision |
| 2026-08-27 | Treat M007 sequences as candidate product features rather than command lists to display | M008 must test whether each relevant sequence has a coherent user goal and visual signals before investing in its interface |
| 2026-08-27 | Use a baseline to select the bounded feature set and learn exact interface forms through use | This keeps the milestone adaptable without making its deliverable vague: a baseline plus real usable interfaces must exist at closeout |
| 2026-08-27 | Supersede the fixed perception-memory-first assumption with an M007 feature baseline | The first proposal may retarget the unchanged work-order node through the normal proposal window; no proposal or implementation has started |

## Closeout

Blocked until every exit criterion is `Met`.

Closeout will produce:

- `closeout.md`;
- a completed-milestone ledger entry;
- a baseline evaluating the relevant M007 sequence families as workbench
  features and recording the bounded implementation selection;
- a human-runnable workbench with task-appropriate interfaces for the selected
  features, backed by real CLI sequence results;
- end-to-end evidence for a primary feature and at least one meaningfully
  different feature through the same workbench;
- explicit recovery, cleanup, safety, and unavailable-dependency limits;
- a residual-risk statement covering deferred M007 candidates, M006 dependency,
  external Metrics UI capabilities, movement authority, transport limits, and
  visual design that remains intentionally adaptable.
