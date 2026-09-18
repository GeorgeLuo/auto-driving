# Proposal Amendment: Historical capability validation source isolation

## Review Kind

Review repair

## Review Question

Can the timeout input-envelope implementation run the required repository suite
without rewriting or weakening M007-09 by validating that historical capability
record against its frozen Git source authority instead of the mutable current
product checkout?

## Reason For Amendment

The accepted timeout proposal in PR #145 requires both a product change in
`cli/automa_cli/app.py` and a passing default repository suite. It also keeps
the accepted M007 live and reachability artifacts historical and outside this
unit. Those requirements cannot all hold under the current M007-09 validator.

At PR #146 head `6a05d8878f07cd6280b1b578011cde9d3eb6a75c`, the focused
timeout matrix passes, but the default suite fails before the M007-09 tests run:

```text
m007_capability_disposition.CapabilityDispositionError:
sealed source file changed or is missing: cli/automa_cli/app.py
```

The failure is deterministic. `load_sealed_report()` reads the accepted
M007-07 source hashes, then compares them with files under the caller's current
working tree. M007-09 subsequently parses that same mutable tree to verify the
sealed possible-statement and possible-arc projection. Any later product edit
therefore invalidates a historical measurement even when every committed
M007-07/M007-09 input and output byte remains unchanged.

That behavior contradicts the active plan's explicit risk treatment:
capability dispositions are historical to the sealed M007-07 report, and later
product commits may change current reachability without updating the candidate
record. A refresh is required only before making a new reachability claim, not
before accepting an unrelated product repair.

Durable evidence establishing the amendment need:

- timeout proposal PR #145 and merge commit
  `02f0d9fc1cf5b85fde4a118f4f7e87b8464ff01c`;
- failing implementation PR #146 at
  `6a05d8878f07cd6280b1b578011cde9d3eb6a75c`;
- failed deterministic test run
  <https://github.com/GeorgeLuo/auto-driving/actions/runs/32818205805/job/97710694179>;
- M007-07 frozen source commit
  `7931fa9a995af5626fabef818f9e28b98c73e299`;
- accepted M007-09 implementation PR #138 and merge commit
  `460e2827bd6b586e75bc698593be064f4c10e6f9`.

The existing failure is sufficient to justify amendment review. No recapture
or redundant live execution is required.

## Contract Delta

PR #145 and its proposal artifact remain immutable and authoritative. The
timeout review question, CLI owner, invalid-value matrix, expected handoff, and
all product behavior remain unchanged. This amendment corrects only the
historical-validation topology required by PR #145's repository-suite gate.

### Separate current evidence from historical source

M007-09 validation has two distinct inputs:

1. **Committed evidence input.** The validator reads the M007-07 report and
   manifest, M007-09 source analysis and grouping, M007-08 authority artifacts,
   and all committed M007-09 record/report/residual/rollup/HTML outputs from the
   current checkout. Their existing schemas, exact keys, canonical bytes,
   recorded digests, derivations, and semantic checks remain authoritative.
2. **Historical source input.** Wherever M007-09 verifies source/configuration
   bytes or derives possible statements and arcs for the sealed source
   universe, it resolves bytes from the Git tree named by the existing frozen
   M007-07 source identity:
   `7931fa9a995af5626fabef818f9e28b98c73e299`. It does not read those bytes from
   current working-tree product files.

The source resolver must:

- accept only normalized repository-relative paths already admitted by the
  sealed source universe or the frozen coverage configuration contract;
- resolve the exact frozen commit without branch, tag, index, or working-tree
  fallback;
- distinguish a missing commit, missing blob, non-blob path, unreadable Git
  object, and source-hash mismatch with a bounded validation error;
- hash the resolved bytes and compare them with the accepted M007-07 per-file
  SHA-256 map before parsing;
- parse possible statements and arcs from those same verified frozen bytes
  under the existing Coverage.py analysis contract; and
- fail closed when the frozen object cannot be proven. It must never silently
  substitute current source, skip the historical test, fetch from the network,
  or bless a new source identity.

Current product-source equality is no longer an M007-09 acceptance predicate.
Changing, deleting, or leaving uncommitted a current owned-product file cannot
by itself alter the result of historical validation. Conversely, source
isolation must not make current committed evidence mutable: any changed,
missing, malformed, non-canonical, digest-mismatched, or semantically
incomplete M007-07/M007-08/M007-09 artifact continues to fail.

### Historical claim boundary

A passing result means only that the committed M007-09 record is a valid
derivation of its accepted frozen inputs. It does not claim that the current
checkout has the same source universe, possible regions, journey reachability,
candidate membership, or capability dispositions.

Refreshing source identity, replaying CLI journey coverage, or publishing a
new capability record remains a separate evidence review unit. The timeout
implementation does not update any M007-07 or M007-09 evidence artifact.

## Trust And Authority Model

### Trusted authorities

- The existing frozen M007-07 source identity, including commit
  `7931fa9a995af5626fabef818f9e28b98c73e299`, relevant-tree identity,
  per-file SHA-256 map, owned source roots, and coverage configuration digest.
- Git object bytes addressed by that exact commit, after each source/config
  blob verifies against the accepted SHA-256 value.
- Existing frozen M007-08 artifact identities and M007-09 schema, canonical
  byte, derivation, and digest rules.
- The committed evidence files presented by the current checkout only after
  they pass those existing checks.

### Untrusted inputs

- Current branch, tag, index, working-tree, and untracked product bytes.
- Caller-supplied paths, Git subprocess output, environment configuration, and
  any implicit current-HEAD resolution.
- A current artifact merely because it is tracked or has a familiar path.

### Guarantees and limits

The amendment guarantees consistency and provenance to the locally available
Git object named by the accepted frozen identity. It covers accidental or
intentional current-tree drift, path substitution, missing objects, malformed
Git responses, and evidence-byte mutation. It does not claim freshness for
current product code and does not defend against a compromised Git executable,
compromised filesystem below the validator, or a break of the recorded
cryptographic identities.

## Evidence Topology And Capture Strategy

The validation path is:

```text
accepted M007-07 source identity
  -> exact frozen Git commit/blob resolution
  -> per-blob SHA-256 verification
  -> Coverage.py statement/arc projection
  -> committed source_analysis.json comparison
  -> sealed journey execution subtraction
  -> committed M007-09 record and derived-output verification
```

Current checkout evidence bytes remain the objects under test. Frozen Git
source bytes are a read-only authority used to verify those objects. Current
product bytes are outside that derivation.

No new canonical evidence capture is authorized. Implementation readiness
requires a deterministic source resolver, focused negative fixtures for every
resolver failure class, unchanged historical artifacts, and a passing default
repository suite with PR #146's `app.py` change present. The resolver may use a
small injectable byte-reader seam for tests, but production validation must use
the exact local Git object and fail if it is unavailable.

## Ownership

| Boundary | Owner after amendment |
| --- | --- |
| Timeout value validation and command/error envelope | Existing `cli/automa_cli/app.py` handler boundary from PR #145 |
| Historical source/config byte resolution | M007-09 capability-disposition validator |
| Historical evidence schemas, canonical bytes, digests, and derivation | Existing M007-07/M007-08/M007-09 validators; unchanged |
| Current reachability or refreshed capability claims | Separate future evidence review unit; not this amendment |
| Regression proof | Focused capability-disposition tests plus PR #145's focused CLI tests and default repository suite |

## Affected Paths

After amendment acceptance, the implementation may additionally touch:

- `docs/milestones/007-cli-operator-usability/tools/capability-disposition/capability_disposition.py`
  for exact frozen Git source/config resolution;
- `tests/milestones/test_capability_disposition.py` for current-tree isolation,
  missing-object, mismatch, traversal, and evidence-drift regressions; and
- the capability-disposition tool README only to document historical validation
  prerequisites and the no-current-HEAD claim.

The original PR #145 implementation paths remain authorized for the unchanged
timeout repair. No M007-07/M007-08/M007-09 evidence, grouping, source-analysis,
record, report, residual, rollup, or derived HTML path is added to the mutable
implementation set.

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| Current `cli/automa_cli/app.py` differs from the frozen source while all historical inputs/outputs are unchanged | Historical validation passes; the default suite proceeds to the timeout tests |
| A different current owned-product file is edited, deleted, or dirty | Historical derivation is unchanged; no current-source freshness claim is made |
| Current checkout happens to equal the frozen source | Same result and record digest as the changed-current-tree case |
| Frozen commit is absent from the local Git object database | Fail closed with the missing frozen commit identity; do not fetch or use HEAD |
| Frozen source path is missing, resolves to a tree/non-blob, or cannot be read | Fail closed and identify the sealed path |
| Resolved frozen blob bytes do not match the M007-07 SHA-256 map | Fail before parsing or record derivation |
| Frozen `.coveragerc` bytes do not match the accepted configuration digest | Fail before source analysis |
| Absolute, parent-traversal, NUL-containing, or non-sealed source path reaches the resolver | Reject before invoking Git object resolution |
| Git resolution returns an error, partial output, or ambiguous revision | Fail closed; no filesystem fallback |
| `source_analysis.json` omits or changes a statement, arc, path, source hash, runtime input, or canonical byte | Preserve the existing failure |
| M007-07 report/manifest, M007-08 authority input, M007-09 grouping/record/report/residual/rollup/HTML is changed or missing | Preserve the existing schema, digest, derivation, or semantic failure |
| Historical validation passes on changed current source | Output remains explicitly historical and does not describe current reachability |
| Timeout matrix contains zero, negative, NaN, or infinity after source isolation | Preserve PR #145's exit-2/no-dispatch behavior and assertions |
| Valid timeout or unrelated downstream `ValueError` after source isolation | Preserve PR #145's pass-through and non-relabeling behavior |

## External Assumptions

- Repository validation runs inside a Git checkout whose local object database
  contains commit `7931fa9a995af5626fabef818f9e28b98c73e299`; GitHub Actions
  already checks out full history with `fetch-depth: 0`.
- A shallow or exported checkout may lack that object. Such an environment is
  unavailable for historical validation and fails with explicit recovery; it
  is not silently accepted.
- The installed Git executable returns exact blob bytes for an exact
  `<commit>:<path>` object request, and the validator independently verifies
  their accepted SHA-256 identities.
- The existing frozen Coverage.py analysis contract remains available. This
  amendment changes the source byte provider, not parser semantics.
- No simulator, browser, worker, network fetch, or external repository is
  needed.

## Non-Goals

- Recapturing M007-07 journey coverage or M007-09 source analysis/evidence.
- Updating candidate membership, group disposition, owner, counts, report
  digest, or derived HTML.
- Claiming that historical M007-09 evidence describes current HEAD.
- Skipping or filtering `test_capability_disposition`, weakening a digest, or
  accepting source drift through an exception allowlist.
- Building a general Git snapshot framework for unrelated historical units.
- Changing PR #145's timeout semantics, output contracts, expected handoff, or
  M007-04 ownership.
- Editing accepted proposal artifacts, cumulative PR #81, the closeout packet,
  or completed-milestone ledger.
- Adding implementation code or tests in this amendment PR.

## File Impact

This amendment PR changes only:

- this additive amendment artifact;
- canonical M007 `plan.md`; and
- generated M007 `plan.html`.

After acceptance, PR #146 may reconcile the original timeout implementation
with the additional capability-disposition validator, focused milestone tests,
and directly corresponding tool README described above. Historical evidence
artifacts remain byte-for-byte unchanged.

## Validation Plan

### Amendment PR

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 -m unittest \
  tests.docs.test_milestone_proposal_workflow \
  tests.docs.test_milestone_planning
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/amend-historical-capability-validation \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

Review must also confirm that the accepted PR #145 proposal, existing evidence
artifacts, exit-criterion state, risk rows, accepted-review ledger, and frontier
map have no diff.

### Reconciled implementation PR

1. Run focused capability-disposition tests proving every source-resolver and
   artifact-drift row above, including a changed current `app.py`.
2. Run the capability-disposition `validate` command and require the existing
   record digest, candidate count, and group count to remain unchanged.
3. Assert the implementation diff contains no M007-07/M007-08/M007-09 evidence,
   grouping, source-analysis, record, report, residual, rollup, or derived HTML
   changes.
4. Re-run PR #145's focused timeout matrix and affected CLI suites.
5. Run the complete deterministic repository suite under coverage.
6. Run workflow validation, Markdown rendering check, and `git diff --check`.

No live system or new evidence capture is required.
