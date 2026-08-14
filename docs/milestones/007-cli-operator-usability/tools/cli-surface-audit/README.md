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
| `claim_map.json` | Semantic citation predicates for hybrid `passed` |
| `live_residuals.json` | Linked `M007-LIVE-*` residuals |
| `frozen_authority.py` | Code-frozen templates, claim map, and #88 source identity |
| `validate_audit.py` | Fail-closed finalizer |
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

## Non-claims

- Cited `passed` is historical, not HEAD re-verification.
- Deferred/blocked US rows are accountable residuals, not green.
- Coverage treatment is not a percentage gate.
- No product repair of LIVE defects in this unit.
