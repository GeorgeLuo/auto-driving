# M007-08 CLI surface and sequence audit

Implements the accepted
[`cli-surface-audit`](../../proposals/cli-surface-audit.md) contract.

## Artifacts

| Path | Role |
| --- | --- |
| `us88_source.md` | Verbatim #88 source blob (content digest frozen) |
| `us88_catalog.json` | Normalized #88 US-01…US-10 authority with per-US anchors and reviewed command deltas |
| `leaf_inventory.json` | Argparse-derived public leaf membership (`kind: action` \| `meta`) |
| `leaf_overlay.json` | Mandatory classification overlay; `supports_json` must match argparse |
| `sequence_registry.json` | US registry bound to catalog digest with dispositions |
| `claim_map.json` | Semantic citation predicates and ordered exact-step trace bindings for hybrid `passed` |
| `live_residuals.json` | Linked `M007-LIVE-*` residuals |
| `frozen_authority.py` | Code-frozen templates, claim/trace map, and #88 source identity |
| `validate_audit.py` | Fail-closed finalizer |
| `frozen_parser.py` | Exact-Git parser-source resolver for the inventory-generation revision |
| `parser_walk.py` / `argv_validate.py` | Membership walk (help → `kind: meta`) and parser-aware argv checks |

### Membership rule

Terminal leaves whose last token is `help` are inventory members tagged
`kind: meta`. Nodes that own optional (non-required) subparsers are also
public terminals: they are tagged `kind: alias` and bound to their explicit
help child, then children are still walked. All other terminals are
`kind: action`. Help-drift compares the action set only.

Evidence output:

- `../../evidence/cli-surface-audit/report.json`
- `../../evidence/cli-surface-audit/rollup.md`

## Run

From the repository root:

```sh
python3 docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/validate_audit.py \
  --write-evidence
```

Exit `0` and `result: pass` are required for Met.

Historical parser validation requires the local Git object
`a989324470e6fc04c9d9678e2337daf47828100b`, the revision already recorded by
`leaf_inventory.json`. The finalizer resolves that exact commit without a
branch, tag, working-tree, or network fallback, verifies the frozen `app.py`
bytes and the complete 100-file historical import tree, and imports that source
in an isolated child process. Current artifact bytes remain the evidence under
validation. A checkout that lacks the frozen object fails closed; CI fetches it
explicitly.

The accepted audit unit remains PR #122 at canonical merge
`5c09fb32dc196b79e90c2a54fde78b896b41cb49`; that acceptance identity does not
replace the earlier parser-generation revision recorded inside its inventory.

## Non-claims

- Cited `passed` is historical, not HEAD re-verification.
- The 49-leaf inventory and parser-aware argv receipts describe the frozen
  inventory-generation revision, not the current checkout's CLI membership.
- Later CLI additions do not rewrite M007 evidence; current CLI claims require
  their own current-head tests or a separately reviewed audit refresh.
- A cited family aggregate cannot promote a row unless its frozen result also
  contains every registered command in order, plus the declared confirmation
  and cleanup predicates; extra diagnostic commands do not replace a missing
  registered step.
- Cleanup must be a distinct post-sequence event. The only frozen overlap is
  US-02's cleanup stop reusing its explicitly identified journey stop; earlier
  matching commands cannot satisfy another row's cleanup.
- Repeated command placeholders bind one concrete identity across the whole
  trace and cleanup, so mixed vehicle IDs or source directories fail closed.
- Deferred/blocked US rows are accountable residuals, not green.
- Coverage treatment is not a percentage gate.
- No product repair of LIVE defects in this unit.
