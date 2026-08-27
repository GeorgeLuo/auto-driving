# Milestone 008 — CLI Decision Workbench

| Field | Value |
| --- | --- |
| Status | Active |
| Milestone branch | `milestone/008-cli-decision-workbench` |
| Cumulative PR | TBD (draft until whole-milestone closeout) |
| Current frontier | None (idle; first proposal not started) |
| Started | 2026-08-26 |
| Action policy | Observation-only; no applied vehicle movement |

Shared planning contract: [README.md](../README.md) · [planning-contract.html](../planning-contract.html)

## Objective

Give an operator a human-friendly workbench for running and inspecting
supported decision-model scenarios. The workbench wraps declared CLI command
sequences, presents their state and results in a form a person can follow, and
exposes decision inputs, outputs, provenance, freshness, authority, recovery,
and cleanup without requiring the operator to know the shell or runtime
topology. The CLI and core engine remain the execution authority so additional
scenarios can be added without creating a second decision implementation.

## Completion Usage

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |
| Primary demonstration | Local workbench and supported M007 CLI runtime with a known observation-only starting state | Open the workbench, choose the supported perception-to-memory scenario, run it, inspect the decision-facing trace, then stop and clean up | One real CLI sequence completes without shell intervention; the workbench shows recognizable step states, real results, provenance and authority, explicit recovery when needed, and safe cleanup with no applied movement | M008-01, M008-02, M008-03, M008-04, M008-05 |
| Inspect a declared scenario | Workbench can read the repository-owned M007 scenario registry | Select a scenario before running it | The workbench shows the scenario identity, prerequisites, safety class, command sequence, confirmation, and cleanup that the CLI will use | M008-01 |
| Inspect the decision process | A supported scenario has staged decision-facing CLI output | Run the scenario and open its inspection view | Inputs and stage outputs are traceable to their source, lifecycle and freshness are visible, and selected proposal or authority is shown or explicitly unavailable; applied movement is never implied | M008-03 |
| Recover a blocked run | A prerequisite, external capability, CLI command, or worker is unavailable | Follow the workbench recovery, or make the named external change, then retry | The failed boundary and exact next action remain visible; the workbench does not silently reconfigure the simulator or leave a worker running | M008-04 |
| Run a second scenario | The primary scenario and the common workbench sequence mechanism are accepted | Select another accepted M007 scenario and execute it | The same workbench path handles the second sequence without a core-engine fork or bespoke hidden state | M008-05 |

## Scope Boundaries

| In scope | Out of scope |
| --- | --- |
| A local human-facing workbench that selects, runs, and inspects declared M007 CLI sequences | Reimplementing perception, memory, decision, or safety logic in the frontend |
| CLI machine-readable results, scenario metadata, step progress, provenance, lifecycle, authority, recovery, and cleanup rendered for human inspection | Replacing the CLI as the execution authority or scraping internal runtime files as a second contract |
| One complete observation-only scenario followed by a second scenario through the same sequence mechanism | Implementing every M007 candidate, a generic workflow builder, or a broad frontend backlog before evidence requires it |
| Decision-facing inspection where the CLI exposes the accepted contract, with explicit unavailable states at dependency boundaries | Completing M006 cross-environment evidence, changing its decision policy, or copying its unfinished work into M008 |
| A maintainable local UI under this repository's frontend surface and the smallest runner needed to invoke the CLI | Redesigning the external Metrics UI, remote or public hosting, authentication, desktop packaging, or movement authority |
| Visual proof that a real CLI journey produces understandable human signals | Final visual polish, animation, or layout coverage as a milestone gate |

## Exit Criteria

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |
| M008-01 | A local workbench can enumerate and launch one declared M007 CLI sequence using the CLI machine-readable contract, with stable scenario identity, prerequisite and safety metadata, and no second execution authority | Unmet | Initial workbench vertical slice has not yet been proposed |
| M008-02 | The primary workbench journey runs the real perception-to-memory lifecycle sequence from a known state and presents a human-recognizable success or failure result with command progress and final cleanup | Unmet | Requires a reviewed implementation and one bounded end-to-end proof |
| M008-03 | The workbench exposes decision-facing inspection for the supported journey, including source inputs, stage outputs, provenance, freshness, selected proposal or authority, and explicit unavailable states, without presenting applied movement | Unmet | Full shadow decision surfaces are accepted on the M006 branch but are not yet part of `main`; M008 must consume an available CLI contract or record the dependency explicitly rather than duplicate it |
| M008-04 | Failed or unsupported steps preserve CLI ownership by surfacing stable error and recovery information, avoiding hidden simulator reconfiguration, and leaving no worker or session mutation after cleanup | Unmet | Recovery and cleanup behavior will be defined against the first real sequence |
| M008-05 | A second accepted M007 scenario runs through the same workbench sequence mechanism without a core-engine or CLI fork, with scenario-specific behavior declared rather than hidden in frontend code | Unmet | Extensibility is unproven until the first slice establishes the common boundary |
| M008-06 | Closeout confirms the primary workbench usage, CLI parity, decision inspection, scenario extension, safety and recovery behavior, and residual limits, then records the next product decision without hiding unfinished work | Unmet | Milestone closeout is selected only after the implementation criteria are Met |

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

## Accepted Review Units

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |

## Open Risks And Unverified Assumptions

| Risk or assumption | Consequence | Resolution path |
| --- | --- | --- |
| The full M006 shadow decision surfaces are accepted on the M006 branch but are not in `main` | A workbench built from `main` cannot claim to inspect the full shadow proposal lifecycle yet | Keep M006 ownership separate; begin with M007 CLI-visible behavior, and consume the decision CLI contract only after it is available on the M008 base or an explicit dependency is accepted |
| The smallest reliable CLI-to-workbench transport is not yet chosen | A premature service or protocol could create make-work before one journey is understood | Let the first behavioral proposal choose the smallest transport that runs the real CLI sequence and preserves machine-readable results |
| Human-friendly presentation may change as operators use the first slice | Freezing a visual layout would turn feedback into rework | Make the behavioral signals and completion usage stable; leave layout and polish adaptable |

## Milestone Decisions

| Date | Decision | Reason |
| --- | --- | --- |
| 2026-08-26 | Open M008 before returning to M006 | M007 established a usable CLI product surface, and the next useful product question is whether those journeys can become a human-facing workbench |
| 2026-08-26 | Keep the engine, CLI, and workbench layered in that order | The workbench should make the CLI understandable and usable without creating a second decision authority |
| 2026-08-26 | Start with one accepted M007 scenario and prove a second through the same mechanism later | A real vertical slice establishes feasibility and scaling evidence without creating a speculative frontend backlog |
| 2026-08-26 | Defer M006 without closing or duplicating it | M006-06 and M006-07 remain unmet on its own branch; M008 is an explicit parallel product milestone with separate scope and safety policy |
| 2026-08-26 | Keep open product issues as candidates until a selected M008 frontier needs one | Reclassifying #89, #90, #91, #93, #101, or #108 now would create issue churn without improving the first workbench decision |

## Closeout

Blocked until every exit criterion is `Met`.

Closeout will produce:

- `closeout.md`;
- a completed-milestone ledger entry;
- a human-runnable workbench demonstration backed by real CLI sequence results;
- decision-facing inspection and recovery limitations stated explicitly;
- evidence that a second accepted scenario uses the same execution boundary;
- a residual-risk statement covering M006 dependency, external Metrics UI
  capabilities, movement authority, transport limits, and visual design that
  remains intentionally adaptable.
