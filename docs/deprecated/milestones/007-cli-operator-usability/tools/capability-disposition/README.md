# M007-09 capability disposition

This tool implements the accepted [capability disposition proposal](../../proposals/capability-disposition.md).
It derives the unreached owned-source set from the sealed M007-07 report and
the verified `source_analysis.json`, then joins that set to the human-authored
`grouping.json`. The tool does not recapture the CLI journeys and does not
change production code.

## Commands

From the repository root:

```sh
# Run only with the frozen CPython 3.11.7/Coverage.py 7.15.2 interpreter.
python3.11 docs/milestones/007-cli-operator-usability/tools/capability-disposition/capability_disposition.py \
  capture-source-analysis

python3 docs/milestones/007-cli-operator-usability/tools/capability-disposition/capability_disposition.py build
python3 docs/milestones/007-cli-operator-usability/tools/capability-disposition/capability_disposition.py validate
```

`capture-source-analysis` is a one-time capture operation. CI and ordinary
validation consume the committed artifact and fail closed if its source,
configuration, runtime, path, SHA, statement, arc, or canonical-byte envelope
does not match the accepted proposal. Historical source and `.coveragerc`
bytes are resolved from the exact frozen M007-07 Git commit recorded by the
sealed report and verified against its per-file SHA-256 map; current product
checkout bytes are not used as a freshness predicate. The local Git object
database must contain that commit, or validation fails closed without falling
back to the current branch, index, working tree, or network.

## Artifacts

| Path | Role |
| --- | --- |
| `grouping.json` | Closed human grouping, reconciliation, owner, disposition, and reason overlay |
| `source_analysis.json` | Sealed Coverage.py possible-statement/possible-arc projection for all 96 owned Python paths |
| `evidence/capability-disposition/record.json` | Canonical M007-09 capability record |
| `evidence/capability-disposition/report.json` | Pass report with derived membership and group summary |
| `evidence/capability-disposition/residuals.json` | Explicit empty residuals plus all disposition candidates |
| `evidence/capability-disposition/rollup.md` | Derived human rollup; not authority |
| `evidence/capability-disposition/record.html` | Semantic HTML projection of the committed record |
| `evidence/capability-disposition/dashboard.html` | Offline operator-capability coverage roadmap with a recursive CLI command tree, operator-outcome coverage, source capability/disposition evidence, and selectable two-column detail views |

The record contains 93 candidate source members. The three fully journey-
reached files are not candidates; the 33 owned Python paths absent from the
coverage report remain members of the sealed source universe and are not
dropped. The record's `remove` value, when used by a later overlay, is a
deletion candidate only. Percentages, line counts, branch counts, and
reachability labels cannot authorize a reason.
