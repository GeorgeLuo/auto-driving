# M007-08 CLI surface and sequence audit

Implements the accepted
[`cli-surface-audit`](../../proposals/cli-surface-audit.md) contract.

## Artifacts

| Path | Role |
| --- | --- |
| `us88_catalog.json` | Immutable #88 US-01…US-10 meaning authority (comment 5169077892) |
| `leaf_inventory.json` | Argparse-derived public leaf membership snapshot |
| `leaf_overlay.json` | Mandatory classification overlay keyed by leaf id |
| `sequence_registry.json` | US registry bound to catalog digest with dispositions |
| `claim_map.json` | Semantic citation predicates for hybrid `passed` |
| `live_residuals.json` | Linked `M007-LIVE-*` residuals |
| `validate_audit.py` | Fail-closed finalizer |
| `parser_walk.py` / `argv_validate.py` | Membership walk and parser-aware argv checks |

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
