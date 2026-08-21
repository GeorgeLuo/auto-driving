# Proposal: Capability disposition outside CLI journeys

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Capability disposition outside CLI journeys |
| Proposal branch | `m007/capability-disposition-proposal` |
| Implementation branch | `m007/capability-disposition` |
| Exit criterion | M007-09 |
| Review kind | Deterministic invariant closure |

## Review Question

Can owned production code not reached by the declared CLI journey set be
grouped by capability and reconciled with tests, other entrypoints, dynamic or
platform paths, and ownership so every group is flagged to expose through CLI,
retain with an explicit owner and reason, or remove through separately reviewed
work, without authorizing feature or deletion solely by a coverage percentage?

This unit is **accountable disposition of unreached owned code**, not product
change. The milestone walks away knowing every such group has an owned
`expose`, `retain`, or `remove` candidate. It does not implement those
candidates.

## Glossary (contract terms)

| Term | Meaning |
| --- | --- |
| **Owned production code** | Tracked Python under the #107 owned roots (the same roots the journey-coverage collector measures). Tests, docs, generated runtime, and lab candidates are not this set. |
| **Declared CLI journey set** | The logical contexts in the sealed M007-07 `report.json` whose IDs begin `m007/journey/`. The current report admits 22 contexts across the primary journey and three continuity families. Bootstrap and support contexts are not journey execution. |
| **Sealed source universe** | The sorted `.py` paths in `subject.source_identity.relevant.files` that are under `inputs.owned_source_roots`, with the per-path SHA-256 values in `inputs.relevant_file_sha256`. This is larger than `report.files`; `report.files` is execution evidence, not the universe. |
| **Source member** | One owned source path plus its sealed source SHA and exact unreached statement/arc sets. A wholly absent path is still a member, even when coverage reports no executable region for it. |
| **Statement region** | A canonical `(path, line)` pair in the executable-statement set produced by the sealed coverage.py source analysis. |
| **Arc region** | A canonical `(path, from_line, to_line)` pair in the possible branch-arc set produced by that analysis. Negative entry/exit endpoints are retained as coverage.py reports them. |
| **Reached** | A statement or arc present in the union of `executed_lines` or `executed_arcs` across the admitted `m007/journey/` contexts for that path. A path occurring only in `report.files` through bootstrap or support context is not reached. |
| **Unreached** | A source member whose path is absent from `report.files`, or whose possible statement/arc sets contain a region absent from the corresponding executed union. |
| **Capability group** | A named cluster of unreached regions that share one product capability and one owner. Not a per-line dump. |
| **Disposition** | Exactly one of `expose`, `retain`, or `remove`. `remove` is a candidate for later review, not a delete in this unit. |
| **Reachability authority** | The sealed #107 report bytes. Percentages in that report are informational and never Met. |

## Proposed Contract

### Sealed input identity

This proposal is bound to the current sealed M007-07 report, not to whatever
source happens to be checked out when implementation starts:

| Input | Frozen value or rule |
| --- | --- |
| Report | `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json`, `integrity.report_sha256 = 51801c7686b247055114109e7462d13cb6702a1c8dcd8990a168f68357015789` |
| Journey manifest | `docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/manifest.json`, SHA-256 `bcb20961c05a850fafc16364f13e0a3bde8ef3a612eca523f35a6c065f515683`; its catalog/context roles are the role-selector authority |
| Source revision | `subject.source_identity.commit = 7931fa9a995af5626fabef818f9e28b98c73e299`; relevant-file tree `e9e708b083bd203e1ca6b058404869e838ea5ad8dc1e7c9466302b9ab873bbe0` |
| Coverage analysis | `subject.coverage_version = 7.15.2`, with the sealed `.coveragerc` settings `branch = True`, `relative_files = True`, the three declared source roots, and `omit = */__init__.py` |
| Source-analysis runtime | `dependency_environment.interpreter`: CPython 3.11.7, `abi = cpython-311-darwin`, `cache_tag = cpython-311`, `executable_basename = python3.11`, `executable_sha256 = 32da055a5f026c1615772517ef6dd70df85fc486862ecf571bec5915897c8b74`, and `executable_path_sha256 = 225380e24ac6bf74d3c88512e50f100ef45cae27e9f30d66f376b5f968894c5e` |
| File universe | Exact sorted paths from `subject.source_identity.relevant.files` whose normalized path is a `.py` file equal to or below one of `inputs.owned_source_roots`; each path's SHA must match `inputs.relevant_file_sha256` |
| M007-08 audit report | `docs/milestones/007-cli-operator-usability/evidence/cli-surface-audit/report.json`, schema `m007_cli_surface_audit_v1`, SHA-256 `11cf7c7696f4995bcc433eff6b5f1d67b4e269e39ad825177d664a5add722b6d` |
| M007-08 leaf inventory | `docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/leaf_inventory.json`, schema `m007_leaf_inventory_v1`, SHA-256 `21efc3a9af9bb551e2bd3b0b949f5ddcc50d7748888d97cd360070983d40d3c4` |
| M007-08 leaf overlay | `docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/leaf_overlay.json`, SHA-256 `41e284ea7284f7ae2c74f312a0dde391330813c6e188cd7e16a391f1d69f869f` |
| M007-08 sequence registry | `docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/sequence_registry.json`, schema `m007_sequence_registry_v1`, SHA-256 `005ef8c7d4a715e72ba721e29ba5e4df7c22e301668fdd0bc1b280da125308c2`, catalog digest `9cf4c8bf139183d10ea51c5b576eb47cef1919a161570d704893b3f7372a0e40` |
| M007-08 residual ownership | `docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/live_residuals.json`, schema `m007_live_residuals_v1`, SHA-256 `a8a0f2c53d230fc56b20fcc0c27391a09e750529028d84922a8a7b67513ca60c` |
| M007-08 catalog snapshot | `docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/us88_catalog.json`, SHA-256 `9cf4c8bf139183d10ea51c5b576eb47cef1919a161570d704893b3f7372a0e40` |

The implementation records the report path, report digest, source commit, and
relevant-file tree digest and fails closed if any of them, the source hashes,
the coverage version, runtime identity, admitted role selector, M007-08 input
manifest, or the owned roots differ. The sealed report currently
contains 96 owned Python paths while `report.files` contains 63 paths; the 33
owned paths absent from `report.files` are intentionally part of the source
universe and must not disappear from the capability record. `report.files` is
used only to obtain per-context execution evidence.

### Journey-role derivation

The role join is closed and is not a union of every context in `report.files`.
Implementation admits exactly the logical context IDs in
`contexts.expected_logical_contexts` and `contexts.observed_logical_contexts`
whose normalized ID matches `^m007/journey/`, and fails closed if those two
sets differ. The current sealed report therefore admits exactly 22 contexts.
`m007/bootstrap/...` and every `m007/support/...` context are excluded,
including `support/cleanup`, `support/precondition`, and
`support/supplemental_capture`. A cleanup command may be admitted only when its
exact logical context is in the sealed manifest/catalog's declared journey
command set; an entry in `manifest.support_commands.cleanup` or an M007-08
sequence's `cleanup` array alone does not promote it. The current report has no
admitted support-cleanup context.

For each source-universe path, implementation obtains the possible statement
and branch-arc sets by analyzing the source at the frozen commit with the
sealed coverage.py/configuration identity. For a path absent from
`report.files`, the executed line and arc sets are empty. Otherwise they are
the unions of that path's context-level `executed_lines` and `executed_arcs`
only for the admitted journey contexts.
The derived unreached sets are therefore deterministic even for an entirely
unrepresented file, a partially reached file, or a file with a missing branch
arc.

### Acceptance statement

An implementation answers the review question only when **all** of the
following hold:

1. **Unreached set is derived, not invented.** Membership is computed from the
   frozen source universe and the sealed report. A human overlay cannot add a
   path outside that universe or drop a path the source/reachability derivation
   marks as unreached. The executed union uses only the closed journey-role
   selector; `report.files` is never used as the source universe or as a
   substitute for that selector.
2. **Every unreached region belongs to exactly one capability group.** Each
   capability-record member is a source path with its sealed SHA,
   `unreached_statements`, and `unreached_arcs`. The member-path set must equal
   the derived candidate-path set exactly; each path occurs once; and each
   statement/arc set must equal possible regions minus the report's executed
   union. A path absent from `report.files` remains a member even if its
   possible-region sets are empty. Omission, partial-file loss, and
   branch-arc loss fail Met. Empty "nothing unreached" is allowed only when
   the derived candidate set is empty.
3. **Every group is reconciled.** Each group has separate, required
   `tests`, `non_cli_entrypoints`, `dynamic_paths`, and `platform_paths`
   reconciliation objects. Each object uses only `present` or
   `not_applicable`: `present` requires one or more stable references and no
   empty reason; `not_applicable` requires an empty reference list and a
   non-empty reason. Unknown statuses, missing objects, blank references, and
   missing `not_applicable` reasons fail. The owner is a structured object
   whose `kind` is `repo_path` or `m007_08_owner`; a `repo_path` must be an
   existing sealed source path/directory containing a member, and an
   `m007_08_owner` must exactly match an owner value in the frozen M007-08
   input manifest. An arbitrary non-empty string is not an owner.
4. **Every group has one disposition and a mechanically decidable reason.**
   `expose` = candidate to add or surface through CLI. `retain` = keep with
   owner and why CLI journeys need not reach it. `remove` = candidate for a
   later deletion review. The reason is a closed `code`, typed `reference`,
   and non-empty `detail`; `code` must be `cli_gap` for `expose`,
   `non_cli_entrypoint`, `dynamic_path`, or `platform_path` for `retain`, and
   `separate_removal_review` for `remove`. The reference must resolve against
   the frozen source/M007-08 authorities and the detail grammar is defined
   below. Unknown reason keys, an untyped reference, and a free-text reason
   scalar are rejected. Thus surrounding prose cannot launder a metric into
   causal authorization; the same negative corpus is exercised for every
   disposition.
5. **This unit does not perform the product work.** No CLI feature, no
   deletion, no move of production code to satisfy a disposition. Those are
   later review units.
6. **Validators and focused tests** enforce derivation, grouping completeness,
   the journey-role selector, the closed record envelope, required reconcile
   fields, typed reference resolution, semantic HTML completeness, and the
   metric-resistant reason grammar, including omission and
   "percent-as-reason" negative fixtures. A rollup that looks complete is not
   Met without those tests.

### Artifact shape

| Artifact | Authority | Contents |
| --- | --- | --- |
| **Reachability input** | Sealed M007-07 `report.json` | Owned roots, per-file executed/unexecuted attribution for the declared journey set |
| **Leaf/sequence context** | Frozen M007-08 input manifest above | CLI-facing names, owners, sequence IDs, and residual labels used when grouping; read-only |
| **Capability record** | This unit | Groups, members, reconcile fields, disposition, owner, reason |
| **Pass report / rollup** | This unit | Derived unreached counts, group list, residuals, explicit non-claims |
| **Derived HTML** | Canonical projection of the committed capability record | Human view with semantic completeness checks; not authority; CSS/layout is not Met |

### Capability record envelope

The capability record has one canonical top-level envelope. The human-authored
grouping input is committed at
`docs/milestones/007-cli-operator-usability/tools/capability-disposition/grouping.json`
with schema `m007_capability_grouping_v1`; its digest is recorded in the
envelope. It may assign candidate paths to stable groups and provide the
closed reconciliation, owner, disposition, and reason fields, but it may not
provide source hashes, possible regions, reached regions, or residual counts.
Those values are derived from the frozen inputs.

The grouping input itself has one closed shape and is the only human-authored
input to the derivation:

```json
{
  "schema": "m007_capability_grouping_v1",
  "groups": [
    {
      "id": "stable-capability-id",
      "name": "Human capability label",
      "member_paths": ["autonomy/example.py"],
      "reconcile": {
        "tests": {"status": "not_applicable", "refs": [], "reason": "No test entrypoint owns this capability."},
        "non_cli_entrypoints": {"status": "not_applicable", "refs": [], "reason": "No other entrypoint is declared."},
        "dynamic_paths": {"status": "present", "refs": ["autonomy/example.py"], "reason": ""},
        "platform_paths": {"status": "not_applicable", "refs": [], "reason": "No platform boundary is declared."}
      },
      "owner": {"kind": "repo_path", "ref": "autonomy/example.py"},
      "disposition": "retain",
      "reason": {
        "code": "dynamic_path",
        "reference": {"kind": "reconciliation_ref", "dimension": "dynamic_paths", "ref": "autonomy/example.py"},
        "detail": "Loaded through the runtime plugin boundary and owned there."
      }
    }
  ]
}
```

The grouping top level has exactly `schema` and `groups`; each group has
exactly `id`, `name`, `member_paths`, `reconcile`, `owner`, `disposition`,
and `reason`. `id` and `name` are non-empty strings. Group IDs are non-empty
stable strings, unique after Unicode NFKC normalization and case-folding, and
groups are sorted by ID. Each group has at least one member; the group list
may be empty only when the derived candidate set is empty. Member paths are
normalized sealed repository-relative paths, sorted canonically, and the
union of `member_paths` must equal the derived candidate-path set with no
omission, duplicate, or extra path. The grouping input contains no
source hashes, region arrays, reached data, residuals, or alternate free-text
fields. Its committed bytes are UTF-8 canonical JSON using the same
`sort_keys: true`, `separators: [",", ":"]`, and exactly-one-trailing-LF
rules as the record; `inputs.grouping_input.sha256` is the SHA-256 of those
bytes.

```json
{
  "schema": "m007_capability_disposition_v1",
  "integrity": {
    "canonical_json": {"sort_keys": true, "separators": [",", ":"], "trailing_lf": 1},
    "digest_projection_omits": ["integrity.record_sha256"],
    "record_sha256": "<sha256 of the canonical record projection>"
  },
  "inputs": {
    "journey_coverage": {
      "report_path": "docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json",
      "report_sha256": "51801c7686b247055114109e7462d13cb6702a1c8dcd8990a168f68357015789",
      "manifest_path": "docs/milestones/007-cli-operator-usability/tools/cli-journey-coverage/manifest.json",
      "manifest_sha256": "bcb20961c05a850fafc16364f13e0a3bde8ef3a612eca523f35a6c065f515683",
      "role_selector": {
        "admit_logical_context_prefix": "m007/journey/",
        "admitted_context_count": 22,
        "excluded_prefixes": ["m007/bootstrap/", "m007/support/"]
      },
      "source_identity": {
        "commit": "7931fa9a995af5626fabef818f9e28b98c73e299",
        "relevant_tree_sha256": "e9e708b083bd203e1ca6b058404869e838ea5ad8dc1e7c9466302b9ab873bbe0",
        "owned_source_roots": ["autonomy", "implementations", "cli/automa_cli"]
      },
      "coverage_analysis": {
        "version": "7.15.2",
        "config_path": ".coveragerc",
        "config_sha256": "67c08cb411118105b4ce373cda5e5a5d559e91fe221b0f35a9c3be011fdc106a",
        "branch": true,
        "relative_files": true,
        "omit": ["*/__init__.py"]
      },
      "source_analysis_runtime": {
        "implementation": "CPython",
        "full_version": "3.11.7 (main, Dec 15 2023, 12:09:56) [Clang 14.0.6 ]",
        "abi": "cpython-311-darwin",
        "cache_tag": "cpython-311",
        "executable_basename": "python3.11",
        "executable_sha256": "32da055a5f026c1615772517ef6dd70df85fc486862ecf571bec5915897c8b74",
        "executable_path_sha256": "225380e24ac6bf74d3c88512e50f100ef45cae27e9f30d66f376b5f968894c5e"
      }
    },
    "m007_08": {
      "input_manifest": [
        {
          "id": "audit_report",
          "path": "docs/milestones/007-cli-operator-usability/evidence/cli-surface-audit/report.json",
          "schema": "m007_cli_surface_audit_v1",
          "sha256": "11cf7c7696f4995bcc433eff6b5f1d67b4e269e39ad825177d664a5add722b6d"
        },
        {
          "id": "leaf_inventory",
          "path": "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/leaf_inventory.json",
          "schema": "m007_leaf_inventory_v1",
          "sha256": "21efc3a9af9bb551e2bd3b0b949f5ddcc50d7748888d97cd360070983d40d3c4"
        },
        {
          "id": "leaf_overlay",
          "path": "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/leaf_overlay.json",
          "schema": "m007_leaf_overlay_v1",
          "sha256": "41e284ea7284f7ae2c74f312a0dde391330813c6e188cd7e16a391f1d69f869f"
        },
        {
          "id": "live_residuals",
          "path": "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/live_residuals.json",
          "schema": "m007_live_residuals_v1",
          "sha256": "a8a0f2c53d230fc56b20fcc0c27391a09e750529028d84922a8a7b67513ca60c"
        },
        {
          "id": "sequence_registry",
          "path": "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/sequence_registry.json",
          "schema": "m007_sequence_registry_v1",
          "sha256": "005ef8c7d4a715e72ba721e29ba5e4df7c22e301668fdd0bc1b280da125308c2",
          "catalog_digest": "9cf4c8bf139183d10ea51c5b576eb47cef1919a161570d704893b3f7372a0e40"
        },
        {
          "id": "us88_catalog",
          "path": "docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/us88_catalog.json",
          "schema": "m007_us88_catalog_v1",
          "sha256": "9cf4c8bf139183d10ea51c5b576eb47cef1919a161570d704893b3f7372a0e40"
        }
      ]
    },
    "grouping_input": {
      "schema": "m007_capability_grouping_v1",
      "path": "docs/milestones/007-cli-operator-usability/tools/capability-disposition/grouping.json",
      "sha256": "<sha256 of the canonical grouping JSON bytes>"
    }
  },
  "residuals": {
    "candidate_member_paths": ["<sorted derived paths>"],
    "assigned_member_paths": ["<sorted group-member paths>"],
    "unassigned_member_paths": [],
    "unresolved_region_refs": []
  },
  "groups": [
    {
      "id": "<stable capability id>",
      "name": "<human label>",
      "members": [{"path": "<sealed source path>", "source_sha256": "<sealed source sha>", "unreached_statements": [], "unreached_arcs": []}],
      "reconcile": {"tests": {"status": "not_applicable", "refs": [], "reason": "<required explanation>"}, "non_cli_entrypoints": {"status": "not_applicable", "refs": [], "reason": "<required explanation>"}, "dynamic_paths": {"status": "not_applicable", "refs": [], "reason": "<required explanation>"}, "platform_paths": {"status": "not_applicable", "refs": [], "reason": "<required explanation>"}},
      "owner": {"kind": "repo_path", "ref": "<sealed source path>"},
      "disposition": "retain",
      "reason": {"code": "dynamic_path", "reference": {"kind": "reconciliation_ref", "dimension": "dynamic_paths", "ref": "<exact reconcile ref>"}, "detail": "<non-authorizing human context>"}
    }
  ]
}
```

The real record replaces angle-bracket placeholders with validated values. The
top-level keys are closed. Groups are sorted by stable `id`; group IDs are
unique and are not assigned from row position; members, paths, refs, and
region arrays are sorted by their canonical path/tuple order. The validator
requires `candidate_member_paths` to equal the derived candidate set,
`assigned_member_paths` to equal the union of group members, and both
`unassigned_member_paths` and `unresolved_region_refs` to be empty for Met.
The record digest is computed over the canonical projection before
`integrity.record_sha256` is inserted.

The six `m007_08.input_manifest` entries above are exact, complete, and sorted
by `id`: the validator rejects an omitted, extra, path-mismatched,
schema-mismatched, or digest-mismatched entry. The `source_identity`,
`coverage_analysis`, and
`source_analysis_runtime` objects are also closed projections of the frozen
values in the sealed M007-07 report; their omission or drift fails Met. The
record's group fields are copied from the validated grouping input, while
source hashes, regions, residuals, and all input identity fields are derived
and cannot be supplied by that input.

### Capability record schema

Every group uses the following closed shape; implementations may add no
alternate free-text fields that influence Met:

```json
{
  "reconcile": {
    "tests": {"status": "present", "refs": ["tests/..."], "reason": ""},
    "non_cli_entrypoints": {"status": "not_applicable", "refs": [], "reason": "..."},
    "dynamic_paths": {"status": "present", "refs": ["autonomy/..."], "reason": ""},
    "platform_paths": {"status": "not_applicable", "refs": [], "reason": "..."}
  },
  "owner": {"kind": "repo_path", "ref": "implementations/..."},
  "disposition": "retain",
  "reason": {
    "code": "dynamic_path",
    "reference": {"kind": "reconciliation_ref", "dimension": "dynamic_paths", "ref": "autonomy/..."},
    "detail": "Loaded through the runtime plugin boundary and owned there."
  }
}
```

`reconcile` has exactly the four named dimensions. A `present` object has a
non-empty `refs` list of stable repository-relative paths or declared
entrypoint/artifact identifiers and has no meaningful `reason` (the serialized
value is the empty string). A `not_applicable` object has `refs: []` and a
non-empty explanation in `reason`. The two statuses are the complete vocabulary;
`unknown`, `pending`, and blank values fail. The validator rejects missing or
extra dimension keys and duplicate references after normalization.

`owner.kind = repo_path` means `owner.ref` is an existing sealed source file or
directory within the owned roots and contains at least one group member.
`owner.kind = m007_08_owner` means `owner.ref` is an exact owner or
`ledger_owner` value in the read-only M007-08 sequence registry or audit
report. These are the only owner forms, so a placeholder such as `x`, `team`,
or `unknown` cannot satisfy ownership by being non-empty.

The `reason` object is the only disposition rationale. Its closed code and
typed reference are causal; `detail` provides human context but cannot
override the code. The only accepted reference forms are:

- `source_member`: an exact sealed source-member path and SHA;
- `reconciliation_ref`: a dimension plus an exact reference in that group's
  reconciliation object;
- `m007_08_sequence`: an exact sequence ID resolved through the frozen
  sequence-registry path and digest; or
- `m007_08_owner`: an exact `owner` or `ledger_owner` value resolved through a
  frozen M007-08 artifact and digest.

The serialized forms are closed: `source_member` carries `path` and
`source_sha256`; `reconciliation_ref` carries `dimension` and `ref`;
`m007_08_sequence` carries `sequence_id` and `registry_sha256`; and
`m007_08_owner` carries `value`, `artifact_path`, and `artifact_sha256`.
Code/reference compatibility is also closed: `cli_gap` uses a
`m007_08_sequence` or `source_member`, `non_cli_entrypoint` uses a
`reconciliation_ref` for `non_cli_entrypoints`, `dynamic_path` uses one for
`dynamic_paths`, `platform_path` uses one for `platform_paths`, and
`separate_removal_review` uses a `source_member` or `m007_08_owner`.

Raw coverage-report paths, percentages, counts, issue URLs, and untyped
strings are not causal references. `reference.kind` must be compatible with
the disposition code and resolve against the recorded input manifest; a
reference to an M007-07 metric artifact cannot authorize a disposition.

All human-authored causal context (`reason.detail` and a
`not_applicable` reconciliation `reason`) is normalized with Unicode NFKC,
case-folding, and whitespace collapse. The validator rejects it when it
contains `%`, `\b\d+(?:\.\d+)?\s*(?:percent|percentage)\b`, a numeric ratio
matching `\b\d+(?:\.\d+)?\s*(?:/|:)\s*\d+(?:\.\d+)?\b`, a numeric
line/branch/statement/arc count in either order, or any of the tokens
`coverage`, `unexecuted`, `unreached`, `untested`, `not covered`,
`never executed`, `line count`, `branch count`, `statement count`, or
`arc count`. The code and typed reference, not any free-text field, determine
the disposition. The same negative corpus runs against `expose`, `retain`,
and `remove`.

Exact repository paths and schema version ids are fixed in implementation under:

```text
docs/milestones/007-cli-operator-usability/tools/capability-disposition/
docs/milestones/007-cli-operator-usability/evidence/capability-disposition/
```

`docs/milestones/007-cli-operator-usability/evidence/capability-disposition/`
is this frontier's declared per-frontier evidence directory. Implementation
commits the sealed capability record, pass report, residual rollup, and
derived HTML of those records in that directory. The HTML is generated from
the committed record bytes, not a fixture or independently authored list. Its
semantic content must expose the record schema and digest, every input path
and digest, the admitted-role selector, candidate/assigned/unassigned
residuals, every group and member including region sets, reconciliation status
and reason, owner, disposition, and typed reason code/reference/detail. A
semantic extractor test compares those fields with the record; layout and CSS
are not Met.

### Disposition rules

| Disposition | Meaning in this unit | Forbidden here |
| --- | --- | --- |
| `expose` | Named CLI gap; later unit may add a leaf | Adding the leaf now |
| `retain` | Keep; journeys need not reach it; owner+reason required | Using "untested" as the only reason |
| `remove` | Candidate for a later deletion review | Deleting or quarantining the code now |

M007-06 closeout may cite this record. It may not treat a `remove` candidate as
already deleted.

## Trust And Authority Model

This unit's universal language applies to **complete grouping and fail-closed
disposition of unreached owned production code**. It does not claim that
unreached code is dead, that reached code is correct, or that a percentage is
a product decision.

| Guarantee class | What this unit claims | What it does not claim |
| --- | --- | --- |
| **Consistency** | The member-path set is the frozen source universe's unreached complement under the closed journey-role selector, with exact statement and arc subtraction; every member is in one group; every group has the four closed reconciliation objects and a legal reason object | That #107 attribution remains true after later product commits without a new capture |
| **Provenance** | The record stores the report digest, source commit/tree, per-member source SHA, coverage/runtime identity, admitted-role selector, exact M007-08 input manifest, grouping-input digest, and record digest | That the owner field proves who should implement a later expose/remove unit |
| **Authenticity** | Validators authenticate source/member/region equality, role-scoped execution, closed reconciliation statuses and owner forms, typed reference resolution, semantic HTML projection, and the metric-resistant reason grammar against the sealed inputs | That `retain` or `remove` is the right product call beyond the recorded reason; review still owns judgment quality |

**Trusted inputs:** sealed M007-07 report; #107 owned-root list; the exact
M007-08 input manifest above; the frozen source-analysis runtime; this unit's
grouping schema and record schema.

**Untrusted / non-authoritative for Met:** coverage percentages; chat claims
that "everyone knows this is lab-only"; test-run coverage as a substitute for
the declared journey set; a later HEAD that no longer matches the sealed
report.

**Claim → authority map:**

| Claim | Authority |
| --- | --- |
| File is owned production | Frozen source-universe paths and per-path SHA values |
| File is reached | Presence of an admitted `m007/journey/` context for that path; support/bootstrap presence is insufficient |
| Statement/arc is reached | Union of context-level `executed_lines` / `executed_arcs` after the closed journey-role selector |
| File/region is unreached | Possible source regions minus the corresponding executed union |
| Group membership complete | Derived member rows and exact region sets equal the union of group members with no overlap |
| Tests / entrypoints / platform | The four separate `reconcile` objects and their stable refs |
| Explicit owner | Closed `repo_path` or `m007_08_owner` object |
| Disposition | Closed group field plus code/typed-reference/detail reason object; metric grammar rejects authorization laundering |
| Record identity | Canonical record envelope, input manifest, grouping-input digest, and record digest |
| Derived HTML completeness | Semantic projection of the committed record bytes compared field-for-field with the record |
| Product expose/delete done | Out of scope; later units |

**Adversaries covered:** omitting an unreached owned file; inventing members
outside the sealed source universe; assigning a file or region to two groups;
dropping a partial-file statement or branch arc; using test execution as
journey reachability; allowing a support-only context such as
`cli/automa_cli/app.py:1662` to count as journey reachability; accepting a
source/hash/report/runtime/M007-08 input mismatch; authorizing a disposition
from a percentage, ratio, line/branch count, or `unexecuted` clause; accepting
an untyped or out-of-authority causal reference; omitting record/residual
fields from HTML; collapsing reconciliation into one free-text field; shipping
a rollup with blank or placeholder owner/reason; performing the product change
in this PR.

**Adversaries excluded / residual:** same-user later mutation of product code
that does not refresh #107 (record stays historical); subjective quality of a
`retain` reason beyond required fields; whether a later unit actually lands.

## Evidence Topology And Capture Strategy

| Claim / non-claim | Authoritative raw evidence | Derivation | Semantic verifier |
| --- | --- | --- | --- |
| Unreached membership | Sealed #107 report + frozen source inventory/config | Possible statements/arcs minus role-selected executed unions, with file-absence handling | Exact path/SHA/member-region equality plus the 22-context role fixture |
| Group completeness | Capability record | Union of member rows and exact region sets | No remainder, no extra, no overlap, including partial/branch mutations |
| Reconcile fields present | Closed group schema | Four dimension objects plus owner object | Omission / unknown status / empty / missing-reason mutation tests |
| Reason is not a metric authorization | Structured reason object | Closed code/typed reference plus normalized detail grammar | Percentage, ratio, line/branch count, and `unexecuted` negatives for every disposition |
| CLI context labels | Frozen M007-08 input manifest | Optional join by path/owner/sequence | Unknown IDs, stale digests, or out-of-manifest refs fail |
| Grouping input | Committed `grouping.json` | Closed group assignments and human disposition fields | Schema, canonical digest, candidate-set parity, duplicate/extra/omitted-path rejection |
| Record and HTML identity | Capability record bytes | Canonical envelope and semantic HTML projection | Input/member/residual/group parity check |
| Non-claim: dead code | — | — | Explicit rollup non-claim |
| Non-claim: HEAD still matches #107 | — | — | Record report digest; drift is residual |

**Capture strategy:**

- **Bounded implementation evidence** only: deterministic derivation, schema,
  and adversarial fixtures. No new live CLI or coverage recapture is required
  for Met.
- **#107 recapture** is out of scope unless the sealed report cannot be read.
  Then stop and amend; do not invent reachability.
- **Freshness:** store the #107 report path and digest. Product HEAD drift
  after that digest is residual, not silent Met.
- **Retained artifacts:** capability record, pass report, rollup, derived
  HTML of that record, test fixtures. Derived CI logs are not sole authority.

Canonical live recapture of journeys is **explicitly unnecessary** for Met.

## Ownership

| Concern | Owner |
| --- | --- |
| Unreached membership | Derivation from sealed #107 report and owned roots |
| Capability grouping and reasons | Human review; schema-enforced required fields |
| Percentage ban, role selection, and completeness | This unit's validators |
| CLI leaf/sequence labels | Frozen M007-08 input manifest |
| Record envelope and semantic HTML | This unit's record/renderer validators |
| Implementing `expose` / `remove` | **Out of scope** — later review units |
| Re-measuring journeys | **Out of scope** — M007-07 remains sealed |
| Re-opening leaf inventory | **Out of scope** — M007-08 |
| Milestone closeout | **Out of scope** — M007-06 |

The capability-disposition validator/record suite is the single Met owner. A
coverage percentage, a leaf dump, or an informal "we will delete this later"
note cannot independently mark M007-09 Met.

## Affected Paths

- `#107` evidence
  `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json`
  is a **read input**. This unit does not rewrite it.
- M007-08
  `docs/milestones/007-cli-operator-usability/evidence/cli-surface-audit/report.json`
  and the exact frozen files listed in the sealed-input table are **read
  inputs** for CLI-facing labels, owners, and sequence references.
- New
  `docs/milestones/007-cli-operator-usability/tools/capability-disposition/`
  owns the grouping input, derivation, schema, validators, rollup, README.
- New
  `docs/milestones/007-cli-operator-usability/evidence/capability-disposition/`
  owns the capability record, pass report, and residual rollup.
- `tests/milestones/` owns deterministic derivation, completeness, and
  percentage-ban tests.
- `autonomy/`, `implementations/`, and `cli/automa_cli/` are **read inputs**
  for path identity only. No product behavior change.

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| Owned path is present in the sealed source inventory but absent from `report.files` and in no group | Met fails; the path must have a member row even if its possible-region sets are empty |
| Group member path is outside the frozen source universe or has the wrong source SHA | Reject |
| Same unreached path appears in two groups | Reject |
| Derived unreached set is non-empty but record says none | Met fails |
| Reached statement/arc is listed as unreached, or an unreached statement/arc is omitted | Met fails |
| Partial file loses one possible statement from `unreached_statements` | Reject |
| Missing branch arc is absent from `unreached_arcs` | Reject |
| Sealed report digest, source commit/tree, coverage/runtime identity, M007-08 input digest, or per-file SHA does not match | Met fails |
| `cli/automa_cli/app.py:1662` is executed only in support cleanup/precondition/supplemental contexts | It remains unreached; support context cannot satisfy the journey-role union |
| A cleanup command appears only in an accepted sequence's `cleanup` array and not as a declared journey command | Exclude it from the admitted role set |
| Test-only execution used to mark a file journey-reached | Reject; tests reconcile `retain`, they are not the journey set |
| Group omits one of `tests`, `non_cli_entrypoints`, `dynamic_paths`, or `platform_paths` | Met fails |
| Reconciliation uses an unknown status, blank ref, duplicate ref, or `present` with no ref | Met fails |
| `not_applicable` reconciliation field has a ref, no reason, or a blank reason | Met fails |
| Owner is a free string, placeholder, unknown M007-08 label, or unrelated repo path | Reject |
| Disposition/reason code pair is invalid, reason keys are unknown, reference is untyped, or reference does not resolve through the frozen authority | Reject |
| Reason detail or a `not_applicable` reason contains a percentage, numeric ratio, line/branch/statement/arc count, or forbidden metric token | Reject for `expose`, `retain`, and `remove` |
| Grouping input omits its closed schema, uses an unknown key, contains source/region/residual fields, or has an invalid canonical digest | Reject |
| Grouping input omits, duplicates, or adds a candidate path outside the derived candidate set | Met fails |
| Grouping input has a blank ID/name, an empty group, or a non-canonical group/member ordering | Reject |
| M007-08 input manifest omits, adds, reorders, or changes one of the six frozen entries | Met fails |
| Record omits top-level input identity, source/config/runtime identity, record digest, candidate/assigned residuals, or canonical group/member fields | Met fails |
| Derived HTML omits an input, residual, group, member, owner, reconciliation, disposition, or typed reason field present in the record | Met fails |
| `remove` group accompanied by deleting the production file | Out of scope; fail the independence of this unit |
| `expose` group accompanied by a new CLI leaf | Out of scope |
| Rollup hides groups with `remove` | Met fails |
| Implementation rewrites #107 report to shrink unreached set | Forbidden |
| Any frozen M007-08 input is rewritten or read from a path/digest outside the manifest | Fail closed; amend or separate the unit |
| Issues #89–#108 treated as this unit's Met | Out of scope; later wants |

## External Assumptions

- The sealed M007-07 report remains readable and is the reachability authority
  for this unit. If it is missing or unverifiable, stop; do not recapture as a
  side quest.
- The sealed M007-07 context identities remain the authority for the
  `m007/journey/` role selector; bootstrap and support contexts do not become
  journey execution because they happen to appear in `report.files`.
- #107 owned roots still name the production set this milestone cares about.
- The frozen M007-08 input manifest and source-analysis runtime remain
  readable and unchanged; drift fails closed rather than selecting a nearby
  inventory or interpreter.
- Non-CLI entrypoints (tests, Pi deploy, lab plugins, Metrics UI) exist and
  may justify `retain`; they do not expand the declared journey set.
- Dynamic import and platform-only modules may be unreached for honest
  reasons; they still need a group.

## Non-Goals

- Executing expose, retain-as-refactor, or remove in production code.
- Numeric coverage gates or expanding #107 measurement.
- Reopening M007-08 accounting or M007-07 capture.
- Product repair of LIVE defects or issues #89–#108.
- Milestone closeout (M007-06).
- Claiming unreached code is dead.
- A second coverage collector.

## File Impact

### Proposal PR only

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/capability-disposition.md` | This contract |
| `docs/milestones/007-cli-operator-usability/plan.md` / `plan.html` | `proposal_in_review` transition |

### Expected implementation PR

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/tools/capability-disposition/` | Derivation, schema, validators, rollup, README |
| `docs/milestones/007-cli-operator-usability/evidence/capability-disposition/` | Capability record, pass report, residual rollup, derived HTML of that record |
| `tests/milestones/` | Completeness, overlap, digest, percentage-ban fixtures |
| Plan handoff on success | M007-09 Met; next-frontier remains empty toward closeout |

No planned product changes under `autonomy/`, `implementations/`, or
`cli/automa_cli/`.

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
  --head-ref m007/capability-disposition-proposal \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

Reviewers confirm proposal-only paths, review kind **deterministic invariant
closure**, Trust/Evidence sections, and no implementation payload.

### Implementation PR (after acceptance)

Deterministic:

- frozen source universe and per-file hashes match the sealed report;
- the admitted journey-role set is exactly the frozen `m007/journey/` context
  set, with bootstrap/support exclusion and the `app.py:1662` regression case;
- the frozen M007-08 input manifest and CPython 3.11.7 source-analysis runtime
  identity match the record;
- unreached member paths ≡ source-universe paths absent from `report.files` or
  containing a missing possible statement/arc;
- every member path appears in exactly one group with exact missing statement
  and arc sets, including wholly absent files, partial files, and branch arcs;
- the four reconciliation dimensions have closed statuses and stable refs;
- owner form and owner reference validate against the sealed source/M007-08
  inputs;
- the closed reason code/typed-reference/detail grammar rejects metric
  laundering for every disposition;
- the committed grouping input matches its closed schema, canonical digest,
  exact candidate-path parity, and the six-entry M007-08 input manifest;
- the record envelope, canonical ordering, residual parity, and record digest
  validate;
- digest of sealed #107 report matches the record;
- rollup lists every group including `remove`;
- derived HTML regenerates from the committed record and passes semantic
  record-to-HTML completeness (layout is not Met);
- no production path diffs.

No live recapture.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Capability disposition outside CLI journeys in PR #{pr}: unreached owned production code derived from sealed M007-07 report; every region in exactly one capability group; tests/entrypoints/platform/owner reconciled; expose/retain/remove candidates with non-percentage reasons; validators reject omission and percentage-as-authorization; derived HTML of that record; tracked evidence under docs/milestones/007-cli-operator-usability/evidence/capability-disposition/",
  "criterion_updates": {
    "M007-09": {
      "status": "Met",
      "evidence": "PR #{pr} groups unreached owned production code from the sealed journey-coverage report, reconciles tests/entrypoints/dynamic-or-platform paths and ownership, and records an owned expose, retain, or remove candidate for every group without using a coverage percentage as authorization"
    }
  },
  "risk_remove": [
    "Coverage absence is not proof that code is dead"
  ],
  "risk_upsert": [
    {
      "risk": "Capability dispositions are historical to the sealed M007-07 report",
      "consequence": "Later product commits can change what is unreached without updating the candidate record",
      "resolution": "Closeout cites the record digest; a later unit recaptures #107 if reachability must be refreshed"
    }
  ],
  "next_frontier": {
    "state": "none",
    "reason": "M007-09 leaves owned expose/retain/remove candidates. Closeout (M007-06) is the remaining milestone unit when the operator is ready; it does not execute those candidates.",
    "revisit_when": "Operator starts milestone closeout, or a later unit implements a named expose or remove candidate."
  }
}
```

### Sequence after this proposal merges

1. Merge this proposal into `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal` with exact-head review receipt.
3. Start `m007/capability-disposition` and implement only this contract.
4. Pass deterministic validation. Do not recapture journeys.
5. On complete Met, accept implementation with an empty next-frontier toward
   closeout. Otherwise stop without promotion.

## Review Kind

**Deterministic invariant closure** — complete grouping of unreached owned
production code and fail-closed rejection of percentage-as-authorization.
Met is an owned candidate record, not product expose/retain/remove.
