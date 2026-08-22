# Proposal: Complete CLI surface and sequence audit

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Complete CLI surface and sequence audit |
| Proposal branch | `m007/cli-surface-audit-proposal` |
| Implementation branch | `m007/cli-surface-audit` |
| Exit criterion | M007-08 |
| Review kind | Broad mechanical rollout |

## Review Question

Can one committed CLI usage registry prove complete parser-leaf and #88 US-01
through US-10 accounting, with every public leaf mapped to realistic usage,
prerequisites, side effects and safety, expected output, owning boundary, and
deterministic or live validation, and every sequence assigned stable commands,
confirmation, cleanup, coverage treatment, and an explicit passed, ready,
blocked, or deferred disposition with owned unlock conditions, while
machine-first and HITL evidence covers safe executable patterns without
running hazardous or external entries unsafely?

This unit is **accountable inventory**, not universal green and not product
redesign. The milestone walks away knowing the status of every public terminal
command and every planned sequence—including honest deferrals—not forcing every
predefined sequence to pass.

## Glossary (contract terms)

| Term | Meaning |
| --- | --- |
| **Program** | The executable that delivers the CLI (`./cli/automa`). |
| **Branch** | A path of space-delimited subcommand tokens naming a node in the tree (for example `vehicles perception`). |
| **Leaf** | A **terminal command**: a branch path with no further required subcommand token; the public end action. Options/flags are not new leaves. |
| **Invocation** | A concrete argv line including options. |
| **Help** | Program-produced documentation for a node (usage, children, options). |
| **Contract** | Written obligation for behavior, inventory, or evidence. |
| **Intent** | Purpose a surface serves; enumeration and contracts attempt to capture it. |
| **Enumeration** | Explicit listing (leaf inventory, sequence registry). |
| **Contract-correct** | Satisfies the stated contract. |
| **Intent-correct** | Faithful to purpose even when residual incompleteness remains visible. |
| **Complete-to-X** | Every item required by authority X appears (for example complete **to** the program's argparse leaves). |

## Proposed Contract

### Acceptance statement

An implementation answers the review question only when **all** of the
following hold:

1. **Leaf inventory (two-layer).** Membership is generated from the public
   program parser (argparse walk of terminal commands). Human **overlay**
   fields classify each leaf without inventing membership. Overlay cannot add
   a leaf the parser does not expose. Every M007-08 leaf dimension is present
   for every leaf (explicit `not_applicable` + reason where a dimension does
   not apply—omission fails Met).
2. **#88-bound sequence registry.** Every US-01 through US-10 row is bound to
   the immutable #88 candidate-catalog authority (committed normalized snapshot
   with source URL/comment id and content digest). Each row reconciles source
   operator question/outcome **and** current commands, prerequisites,
   confirmation, cleanup, coverage treatment, disposition, and completeness.
   Missing, duplicated, swapped, or altered source mapping fails Met. Id-only
   inventing is not reconciliation.
3. **Parser-aware command validation.** Every current command template in the
   sequence registry (including deferred/blocked rows that declare commands)
   is validated against the public argparse tree at the inventory revision:
   leaf identity, required positionals, option names/ownership, choices, and
   overall argv shape after safe placeholder normalization. Unknown, renamed,
   missing-required, or wrong-level flags fail Met. Leaf-id resolution alone
   is insufficient.
4. **Help cross-check.** Help is checked against the leaf set at least as a
   tracked audit report; Met may harden help inclusion once stable.
5. **Hybrid evidence for `passed` with semantic citation.** Sealed journeys
   may be `passed` only by citation of the **canonical** prior result that
   establishes **that US/family claim** (fixed path(s), schema, and pass
   predicates—including overall/family status and applicable finalizer,
   cleanup/restore, and HITL fields), or by **new** machine-first evidence
   under this unit. Digest-only allow-lists that accept incomplete rehearsals
   fail Met. #107 coverage alone never justifies `passed`. Cite-backed rows do
   **not** require re-run or re-HITL when this unit does not re-open those
   journeys' business logic.
6. **Visible residuals.** Deferred/blocked sequences, explicit N/A fields,
   unmeasured coverage, and known LIVE defects appear in a **human-facing
   audit rollup** (and ideally CI summary). Ambiguity is allowed only when
   **located as an explicit value**; silent omission fails Met.
7. **Validators and focused tests** enforce schema, membership, #88 binding,
   argv shape, disposition rules, and semantic citation—including
   omission-mutation tests for every required field and a negative fixture for
   incomplete machine-only continuity evidence. Deterministic tooling alone
   does not Met if US accounting or residual linkage is incomplete.

### Artifact shape (two registries)

| Artifact | Authority | Contents |
| --- | --- | --- |
| **#88 catalog authority** | Immutable committed snapshot | Normalized US-01…US-10 source entries (operator question/outcome, source narrative hooks) plus provenance: GitHub source URL/comment id and sha256 of the normalized snapshot bytes |
| **Leaf inventory** | Argparse membership + overlay | Every public leaf id, path, help summary skeleton; **all** overlay dimensions required (below)—no optional M007-08 fields |
| **Sequence registry** | #88-bound rows + machine validation | One row per US-01…US-10 bound to catalog source id/digest; current commands (argv-validated), prerequisites, confirmation, cleanup, coverage treatment, disposition, completeness, provenance when passed, owner/unlock when non-green |

Exact repository paths and schema version ids are fixed in implementation under:

```text
docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/
docs/milestones/007-cli-operator-usability/evidence/cli-surface-audit/
```

Committed generated leaf skeleton (or equivalent reviewable snapshot) must make
PRs that add commands show inventory diffs. The #88 catalog authority snapshot
is committed under the tools or evidence tree and is the sole meaning authority
for US identity (not free-form registry prose).

### #88 catalog authority and sequence binding

Source authority for sequence **meaning** (not optional commentary):

| Field | Requirement |
| --- | --- |
| **Origin** | PR #88 issue comment [5169077892](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5169077892) (“Prospective README appendix: usage sequences and human confirmation”), candidate catalog US-01…US-10 |
| **Immutable artifact** | Committed normalized snapshot (YAML/JSON) of every US entry’s stable id, operator question, primary human confirmation intent, and source narrative anchors |
| **Provenance** | Exact source URL + comment id + sha256 of normalized snapshot bytes recorded in the audit pass report |
| **Row binding** | Every sequence registry row carries `source_us_id`, `catalog_digest`, and reconciled fields derived from that entry |

Semantic reconciliation (Met fails if any fails):

- set equality: registry US ids ≡ catalog US ids (no missing, extra, or duplicate);
- no swap/alter: registry operator question/outcome text (or normalized form)
  matches the bound catalog entry; renaming content under another id fails;
- current commands, prerequisites, primary confirmation, and cleanup are
  present and argv-validated even when disposition is non-green;
- usefulness may still yield `deferred`/`blocked`—that is disposition, not
  license to detach from the catalog entry.

Implementation freezes the normalized schema; proposal requires the binding
and fail-closed Met rules above.

### Leaf membership and overlay

- **Leaf id:** stable dotted path of subcommand tokens after the program name
  (for example `vehicles.perception.apply`). Frozen for sequence binding.
- **Terminal only:** groups and pure routing nodes are not leaves. `help` meta
  nodes are excluded from the leaf set or tagged `kind: meta` by a single
  documented rule—implementation picks one rule and tests it.
- **Overlay fields (all required for Met).** Every leaf row must include each
  dimension; use closed enums plus explicit `not_applicable` with a non-empty
  reason where a dimension does not apply. **Omission fails Met.** There is no
  residual-policy escape that allows incomplete overlays to pass.

  | Dimension | Closed values / rule |
  | --- | --- |
  | usage pattern(s) or support class | pattern id(s), or `unsupported` / `deprecated` |
  | prerequisites | text or structured list, or `not_applicable` + reason |
  | side effects | text/list, or `not_applicable` + reason |
  | safety class | closed safety enum (implementation freezes set; must cover observe-only, local_write, live_mutation, hazardous/external, etc.) |
  | expected output / output contract | pointer or summary for human and/or `--json`, or `not_applicable` + reason |
  | owning boundary | non-empty owner string |
  | validation class | `deterministic` \| `live` \| `documented_only` \| `unsafe_not_executed` |
  | open finding links | list (may be empty) of LIVE/defect ids |

- **Help check:** produce a normalized help-derived set and report drift versus
  the argparse leaf set. Soft (report-only) is sufficient for first Met if the
  report is tracked; hard inclusion (`every leaf discoverable via help` or
  `help ⊆ parser`) may be enabled when flake-free.

### Command template argv validation

For every command template listed on any sequence row (including
`deferred`/`blocked`/`ready` templates):

1. Substitute only documented placeholders (for example `{vehicle_id}`) with
   fixture tokens that preserve argv structure; placeholders must not erase
   option names or invent free-form tokens that bypass checks.
2. Parse the full argv against the public program argparse tree at the
   inventory generation revision (same tree as leaf membership).
3. Fail Met on: unknown leaf path; unrecognized options; options attached to
   the wrong parser level; missing required positionals or options; invalid
   choices; malformed multi-command steps.
4. Record validation receipts in the pass report (template id, normalized argv,
   leaf id, ok/fail reason).

Leaf-id substring matching or “first token path exists” is not a sufficient
verifier.

### Sequence disposition and completeness

Two first-class axes:

| Axis | Closed values (minimum) |
| --- | --- |
| **disposition** | `passed` \| `ready` \| `blocked` \| `deferred` |
| **completeness** | `stub` \| `template` \| `catalog_ready` \| `evidenced` |

Rules:

| Rule | Requirement |
| --- | --- |
| Set completeness | Every US-01…US-10 id exists exactly once **and** binds to the #88 catalog authority |
| Catalog fidelity | Operator question/outcome reconciled to bound source entry; swap/alter fails Met |
| Non-green floor | Unrun / unproven rows are at least **`template`**: current commands (argv-validated), prerequisites, safety class, primary confirmation, cleanup, execution intent (`never` \| `machine_only` \| `hitl` \| `blocked`) |
| Coverage treatment | **Required** closed value: `measured` \| `unmeasured` \| `not_applicable`, plus evidence pointer or reason |
| Owner + unlock | Required for `blocked` and `deferred`; unlock must be concrete (artifact, capability, corpus, external issue)—not “later” |
| `passed` | Requires **`completeness: evidenced`** plus hybrid provenance with **semantic** citation or new execute (below). Forbidden at template-only |
| Usefulness | Audit may conclude a planned sequence is less useful than expected; disposition stays non-green with reason **without** detaching from the catalog entry |
| Green appearance | Unrun rows must not present as green in the rollup |

**US-06, US-07, US-09, US-10** follow the non-green floor unless already
evidenced under hybrid rules. US-10 typically remains `blocked`/`deferred` with
an external or labeled-input unlock.

### Hybrid `passed` binding and HITL

| Row type | How `passed` is earned |
| --- | --- |
| Already sealed under #88 / #100 for **that** US/family claim | **Semantic cite** (below)—not digest-only |
| Newly asserted green by this unit | **Execute:** machine-first under `evidence/cli-surface-audit/`; HITL **only** if the row declares visual confirmation |
| #107 report | Coverage annotation only—never sole `passed` authority |

**HITL non-requirement:** Valid semantic cites and inventory-only/tooling work do
not force re-HITL or full re-run of sealed journeys. M007-08 Met must not depend
on lab re-testing of historical green when this unit does not re-open those
journeys' business logic.

#### Semantic citation rules

For each US (or mapped continuity family) that uses `evidence_mode: cited`, the
implementation freezes a **claim map** entry with:

| Element | Requirement |
| --- | --- |
| Canonical path(s) | Exact repository-relative result/evidence path(s) for that claim |
| Schema / identity | Expected result schema or document identity |
| Pass predicates | Parsed fields that must hold—for example overall `result: pass` (or equivalent), required family aggregate `passed`, finalizer ok, cleanup/restore ok, and HITL complete when the sealed unit required visual confirmation |
| Source revision | Accepted PR / commit identity as recorded in that evidence package |
| Leaf presence | Command tokens in the registry template still exist as leaves **and** still pass argv validation |

On-disk sha256 freshness of the cited bytes is necessary but **not sufficient**.
Reject at least:

- incomplete or rehearsal packages (for example
  `evidence/cli-scenario-continuity/machine-only-session/result.json` with
  `result: incomplete` and incomplete family/HITL fields)—required **negative
  fixture**;
- unrelated artifacts that merely share a digestable path under the milestone;
- family mismatch (citing offline family evidence for a live-config US);
- missing finalizer/cleanup/restore/HITL fields when the sealed contract
  required them for that claim.

**Default claim map (implementation may only narrow with amendment, not
weaken):**

| Sequence / claim | Canonical citation source (minimum) |
| --- | --- |
| US-01 / US-02-class | #88 formal live-acceptance `result.json` (and linked evidence) with acceptance pass—not exploratory-only comments |
| US-03-class / `continuity.offline_perception` | #100 continuity **authoritative** `result.json` (session that established required-family `passed`), not machine-only incomplete rehearsal |
| US-04-class / `continuity.live_config_swap` | Same #100 authoritative package; require family aggregate `passed` and US-04 restore verification fields |
| US-05 / US-08-class / `continuity.memory_lifecycle` | Same #100 authoritative package; require family aggregate `passed` |
| US-06 / US-07 / US-09 / US-10 | No #100 required-family pass by default; `passed` only via new execute evidence or a future sealed unit—not by citing incomplete optional-family rows |

Record on each `passed` cite row: `evidence_mode: cited`, claim-map id, path(s),
sha256, source PR/unit, disposition time/identity, `head_claim: historical`
by default.

### Coverage linkage

- M007-08 does **not** require expanding #107 measurement to every leaf.
- Every **sequence** row **must** set `coverage` to exactly one of
  `measured` \| `unmeasured` \| `not_applicable`, with evidence pointer (#107
  context id(s)) or reason.
- Leaf rows may record coverage treatment only as annotation when useful; it
  does not replace required leaf dimensions above.
- Unmeasured ≠ missing from inventory. Hidden “assumed measured” fails Met.
- #107 non-claims remain binding: no correctness, dead-code, or percentage gate.

### Known LIVE / exploratory defects

Import the current confirmed exploratory ledger (at minimum
`M007-LIVE-001`…`M007-LIVE-005` from live-acceptance exploratory findings) as
**first-class residuals**:

- each id linked to leaf and/or pattern/sequence row(s);
- owner + disposition (`deferred` \| `wontfix` \| `fixed_elsewhere` \| …);
- appear in the rendered rollup.

**Met** requires accounting and linkage, **not** product repair. Fixing defects
is a separate review unit.

### Rendered rollup (signal must not be lost)

The implementation produces a human-scannable audit summary (markdown and/or
CI step summary) including at least:

- leaf counts: total, classified, residual unclassified;
- sequences by disposition × completeness;
- cited vs executed `passed` counts;
- deferred/blocked with owner and unlock one-liners;
- open LIVE findings still residual;
- help-drift report status;
- explicit non-claims (historical cite ≠ HEAD re-verification; template ≠
  product commitment).

### Acceptance owner

The audit finalizer/validator suite is the single Met owner for M007-08. A
partial leaf dump, an incomplete US table, or a coverage report cannot
independently mark the criterion Met.

## Trust And Authority Model

This unit's universal language applies to **inventory accounting**
(enumeration-complete to the program's public leaves; exhaustive US-01…US-10
rows; fail-closed schema/cross-check/citation validators). It does **not**
claim that every sequence is behaviorally green at HEAD or that every leaf is
tested.

| Guarantee class | What this unit claims | What it does not claim |
| --- | --- | --- |
| **Consistency** | Leaf membership is consistent with the imported public argparse tree at the inventory generation revision; sequence templates that claim leaf ids resolve to that set; cited digests match on-disk bytes when validated; disposition/completeness rules are internally consistent | Continuous consistency of product behavior with historical live passes after later commits |
| **Provenance** | `passed` rows carry `evidence_mode` (`cited` \| `executed`), refs (path + sha256), and disposition identity; generated leaf rows carry generator identity/revision; residuals name owners | That a citation proves who authored the original live session beyond the sealed artifact's own provenance |
| **Authenticity** | Machine validators authenticate **inventory claims** against parser structure and digest-bound citations. New `executed` rows authenticate only the session this unit records | That cite-backed `passed` authenticates current HEAD operator outcomes without a new execute path |

**Trusted inputs:** public program parser tree; committed normalized #88
catalog snapshot (with comment provenance); canonical #88/#100 result packages
named by the claim map; #107 and exploratory-finding artifacts under the
milestone evidence tree; schemas and validators owned by this unit.

**Untrusted / non-authoritative for Met:** ad-hoc chat claims of green;
coverage percentages; help text alone as membership authority; unlinked
“everyone knows about LIVE-00x” memory.

**Claim → authority map (externally visible):**

| Claim | Authority |
| --- | --- |
| Leaf exists | Argparse walk of public program |
| Leaf meaning (safety, pattern, owner) | Human overlay under review |
| Sequence meaning (US id) | Committed #88 catalog snapshot + digest + comment provenance |
| Sequence disposition | Sequence registry + rules in this contract |
| Historical `passed` | Semantic claim map over sealed #88/#100 result predicates + argv-valid leaves |
| New `passed` | This unit's machine-first (and HITL if visual) evidence |
| Coverage treatment | Required enum on each sequence row |
| LIVE residual | Linked finding id + owner on inventory rows |

**Adversaries covered:** silent omission of a parser leaf or US id; inventing
or swapping #88 catalog meaning; inventing overlay leaves; leaf-id-only command
checks that accept invalid argv; promoting `passed` from incomplete rehearsals
or digest-only cites; using #107 as sole behavior pass; omitting required
fields; dropping LIVE ids from residual rollup; unsafe execution for
completeness.

**Adversaries excluded / residual:** same-user or later mutation of product code
that does not touch inventory files (historical cite stays green); intentional
help wording drift until hard help gate is enabled; subjective “usefulness”
judgments quality (review-owned, not machine-proved).

## Evidence Topology And Capture Strategy

| Claim / non-claim | Authoritative raw evidence | Derivation | Semantic verifier |
| --- | --- | --- | --- |
| Membership complete-to-parser | Importable public parser | Generator walk → leaf ids | Equality of generated set to walk; no unknown overlay ids |
| Overlay classified | Committed overlay fields | Schema fill | Required-field validators + residual counts in rollup |
| US row reconciled | #88 catalog snapshot + sequence registry | Bind source id/digest; reconcile question and current commands | Set equality; no swap; argv validation; disposition/completeness rules |
| Command templates current | Public argparse tree + registry argv | Placeholder-normalized parse | Full shape validation receipts |
| `passed` (cited) | Canonical sealed result for that claim map entry | Parse path/schema/predicates + sha256 | Semantic pass predicates; reject incomplete machine-only; leaf argv still valid |
| `passed` (executed) | New session under evidence/cli-surface-audit | Runner receipts | Machine-first (HITL if declared visual) |
| Coverage treatment | Sequence field + optional #107 contexts | Required enum | Never sole `passed` verifier |
| LIVE residual | Exploratory ledger + linkage table | Import + link | Every imported id present, owned, leaf-linked |
| Help drift | Help walk vs leaf set | Report (soft or hard) | Documented inclusion rule when hard-gated |
| Non-claim: HEAD still green for cited rows | — | — | Explicit `head_claim: historical` in rollup |
| Non-claim: template is roadmap | — | — | Non-goals / rollup non-claims |

**Capture strategy:**

- **Bounded implementation evidence** for generator, schemas, validators,
  citation fixtures, and rollup (deterministic tests in the implementation PR).
- **Citation capture** reuses existing milestone evidence; no live lab required
  for cite-only Met.
- **New live capture** only when this unit newly marks a visual or live
  sequence green; readiness requires runner catalog binding, safety class
  allowing execution, and confirmation standard already used in M007-10.
- **Freshness:** inventory generation revision and cited digests are recorded;
  product HEAD drift after citation is residual risk, not silent invalidation
  of historical cite.
- **Retained artifacts:** committed leaf snapshot, overlay, sequence registry,
  pass report, residual rollup, optional new session receipts. Derived CI logs
  are not sole authority.

Canonical live re-capture of #88/#100 journeys is **explicitly unnecessary** for
Met under hybrid cite rules.

## Ownership

| Concern | Owner |
| --- | --- |
| Public leaf membership | Generator over the program argparse tree |
| Leaf meaning (overlay) | Human review; schema-enforced required fields |
| US-01…US-10 definitions and dispositions | Sequence registry + reviewers |
| #88 catalog snapshot | Immutable committed authority + digest in pass report |
| Cite integrity for sealed journeys | Semantic citation validator (claim map, predicates, digest, argv-valid leaves) |
| Command template currency | Parser-aware argv validator |
| New machine/HITL evidence for newly green rows | Live session runner + this unit's evidence tree |
| Journey coverage measurement | Existing #107 tooling (annotation only here) |
| Known LIVE defect linkage | Residual table keyed to leaf/sequence ids |
| Capability expose/retain/remove | **Out of scope** — M007-09 |
| Product repair of LIVE defects | **Out of scope** — separate units |

## Affected Paths

- `cli/automa` / `cli/automa_cli/` are **read inputs** for parser walk; this
  frontier does not require product behavior changes. If inventory needs only
  registration introspection APIs, prefer non-behavioral access; do not hide
  product changes without amendment.
- Accepted catalogs and evidence under
  `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/`
  and `evidence/live-cli-acceptance/`, `evidence/cli-scenario-continuity/`,
  `evidence/cli-journey-coverage/` are **citation inputs**.
- New
  `docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/` owns
  generator, schemas, validators (membership, overlay, #88 bind, argv, semantic
  cite), rollup, README, and the committed normalized #88 catalog snapshot.
- New
  `docs/milestones/007-cli-operator-usability/evidence/cli-surface-audit/`
  owns pass report, digests, residual rollup, optional new session receipts.
- `tests/milestones/` (or `tests/docs/` if purely planning-adjacent) owns
  deterministic schema, generator, citation, and cross-check tests.
- Exploratory defect source:
  `docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/exploratory-findings.md`
  (and any continuity-disposition records already committed).

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| New leaf registered in argparse but missing from inventory | Membership completeness fails Met |
| Overlay invents a leaf id not in argparse set | Reject |
| Parser leaf present, any required overlay field omitted | Met fails (no residual-policy escape) |
| Overlay field set to `not_applicable` without reason | Met fails |
| Sequence references unknown leaf id | Cross-check fails |
| Command template has `--bogus` or missing required positional | Argv validator fails Met |
| Flag attached to wrong parser level | Argv validator fails Met |
| US-06 row is name-only stub | Completeness floor fails |
| US-06 content swapped with US-07 under stable ids | Catalog fidelity fails Met |
| Registry US set missing catalog id or adds extra id | Catalog set equality fails Met |
| US-07 `deferred` with unlock “later” | Review rejects; validators flag non-concrete unlock where machine-checkable |
| Sequence row omits `coverage` key | Met fails |
| US-04 marked `passed` without semantic cite package | `passed` rules fail |
| US-04 `passed` by citing machine-only incomplete continuity `result.json` with matching digest | Reject (required negative fixture) |
| US-04 `passed` by citing authoritative #100 package with family `passed`, restore ok, finalizer ok | Allowed; no re-HITL required |
| Operator re-hashes cite without re-run for an `executed` row | Forbidden; only cite mode uses historical artifacts |
| #107 report used as sole `passed` proof | Reject |
| Help invents a command not in argparse | Help-drift report fails or hard gate fails if enabled |
| Hazardous leaf forced live “for completeness” | Forbidden; `unsafe_not_executed` / execution `never` |
| Known `M007-LIVE-001` absent from residual table | Met fails |
| LIVE defect present but unlinked to any leaf | Met fails |
| Deferred US hidden from rollup | Met fails |
| Unmeasured coverage treated as missing leaf | Forbidden confusion; coverage is required enum, not membership |
| Template sequence treated as product commitment / roadmap | Non-claim; docs must state candidate-only |
| Cite `passed` read as “verified at HEAD” | Non-claim; `head_claim: historical` default must be visible in rollup |
| Implementation changes CLI product behavior to “make inventory easier” | Stop; amend proposal or separate product unit |
| M007-09 expose/retain/remove decided in this PR | Out of scope; reject scope creep |

## External Assumptions

- The public CLI is built through a single importable argparse tree reachable
  from the `automa` entrypoint (or a documented equivalent). Dynamic
  env-only subcommands, if any, are declared and either enumerated by the
  same walk or explicitly excluded with owner.
- PR #88 and PR #100 remain behavioral authority for sealed journeys; this
  unit does not re-litigate their Met criteria.
- #107 remains informational attribution; its non-claims hold.
- Metrics UI / Chase is required only if this unit newly executes visual or
  live sequences; cite-only Met path does not require lab access.
- Exploratory LIVE ids listed in the live-acceptance exploratory ledger remain
  the initial import set unless implementation discovers additional
  already-confirmed tracked findings with stable ids.

## Non-Goals

- Capability disposition (M007-09) or milestone closeout (M007-06).
- Product CLI features, redesign, deletion, or must-fix of LIVE defects.
- Re-opening M007-05 / M007-10 acceptance or forced re-HITL of sealed journeys.
- Numeric coverage gates or full-tree coverage expansion.
- Unsafe execution of movement/hardware/destructive leaves for inventory.
- CLI package-tree refactor to mirror the command tree (may follow later using
  this inventory as the map; not required for Met).
- Zero residual ambiguity; only **known** residual location is required.

## File Impact

### Proposal PR only

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/cli-surface-audit.md` | This contract |
| `docs/milestones/007-cli-operator-usability/plan.md` / `plan.html` | `proposal_in_review` transition |

### Expected implementation PR

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/` | Generator, schemas, validators, rollup, README |
| `docs/milestones/007-cli-operator-usability/evidence/cli-surface-audit/` | Pass report, digests, residual summary, optional sessions |
| Leaf inventory + sequence registry + #88 catalog snapshot | Committed membership, overlays, US registry, catalog digest provenance |
| `tests/milestones/` (and/or docs tests) | Schema, walk, #88 bind/swap, argv mutations, semantic cite (incl. machine-only negative), residual/omission tests |
| Plan handoff on success | M007-08 Met; promote next frontier toward M007-09 |

No planned product changes under `autonomy/`, `implementations/`, or
`cli/automa_cli/` behavior. Introspection-only imports are allowed.

## Validation Plan

### Proposal PR

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 -m unittest \
  tests.docs.test_milestone_proposal_workflow \
  tests.docs.test_milestone_planning
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/cli-surface-audit-proposal \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

Reviewers confirm proposal-only paths, handoff to M007-08, review kind
**Broad mechanical rollout**, and no implementation payload.

### Implementation PR (after acceptance)

Deterministic:

- generator produces leaf set ≡ public argparse terminals;
- overlay and sequence schemas validate;
- every US-01…US-10 present with disposition/completeness rules;
- cross-checks (sequence→leaf, optional help drift);
- citation allow-list and digest freshness fixtures;
- LIVE residual linkage fixtures;
- rollup generation includes residual counts;
- adversarial rows that do not need live UI.

Optional live (only for newly executed `passed` claims):

- machine-first runner sessions under this unit's evidence tree;
- HITL only when the sequence declares visual confirmation.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Complete CLI surface and sequence audit in PR #{pr}: argparse-derived leaf inventory with mandatory overlay dimensions; committed #88 catalog authority snapshot and US-01 through US-10 registry bound to it; parser-aware argv validation; semantic hybrid passed citation (rejecting incomplete rehearsals) or new execute evidence; required coverage treatment enums; validators and cross-checks; help-drift report; linked LIVE residuals without product repair; rendered residual rollup; tracked evidence under docs/milestones/007-cli-operator-usability/evidence/cli-surface-audit/",
  "criterion_updates": {
    "M007-08": {
      "status": "Met",
      "evidence": "PR #{pr} delivers complete-to-parser leaf accounting with all M007-08 leaf dimensions, #88-bound US-01..US-10 reconciliation with argv-valid current commands, semantic cite/execute passed rules, required coverage treatment, and visible residuals for deferred work and known LIVE defects"
    }
  },
  "risk_remove": [
    "US-06, US-07, and US-09 have family IDs but no committed sequence/disposition entries, while US-10 is only plan-level deferred"
  ],
  "risk_upsert": [
    {
      "risk": "Cited sequence passed status is historical, not continuous HEAD verification",
      "consequence": "Regressions after #88/#100 may not be visible in the audit registry until a separate re-proof",
      "resolution": "Optional live smoke or product-unit re-proof when journeys are reopened; residual risk at closeout"
    }
  ],
  "next_frontier": {
    "state": "none",
    "reason": "Capability disposition outside CLI journeys (M007-09) is promoted only after this audit marks M007-08 Met with complete leaf and US-01 through US-10 accounting and visible residuals.",
    "revisit_when": "Every capability group outside declared journeys has an owned expose, retain, or remove candidate before closeout."
  }
}
```

### Sequence after this proposal merges

1. Merge this proposal into `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal` with exact-head review receipt.
3. Start `m007/cli-surface-audit` and implement only this contract.
4. Pass deterministic validation; run new live/HITL only for newly green claims.
5. On complete Met, accept implementation and promote the plan's next frontier
   toward M007-09. Otherwise stop without promotion and keep exact residual
   evidence.

## Review Kind

**Broad mechanical rollout** — faithful application of inventory/registry,
validator, and evidence-citation patterns already used in M007 tooling. Met is
complete honest accounting with loud residuals, not universal green or live
re-acceptance of sealed journeys.
