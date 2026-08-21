# M007-09 capability disposition

Result: `pass`

Record: `docs/milestones/007-cli-operator-usability/evidence/capability-disposition/record.json` (`81ce4993fe8624bbc818bcad7142dafb78e2be1ef6c45a6115ae535a51477e6f`)

This rollup is a derived human view. The record and validators are the
authority. Unreached does not mean dead, and a disposition does not
implement an expose or remove candidate.

## Membership

- Sealed source members: 96
- Admitted journey contexts: 22
- Candidate members: 93
- Assigned members: 93
- Unassigned members: 0
- Unresolved region references: 0

## Capability groups

| ID | Members | Disposition | Owner | Reason code |
| --- | ---: | --- | --- | --- |
| `autonomy-decision-runtime` | 11 | `retain` | `repo_path:autonomy` | `non_cli_entrypoint` |
| `autonomy-perception-plugins` | 9 | `retain` | `repo_path:autonomy/perception` | `dynamic_path` |
| `autonomy-vehicle-boundary` | 2 | `retain` | `repo_path:autonomy/vehicle` | `platform_path` |
| `cli-operator-surfaces` | 25 | `expose` | `repo_path:cli/automa_cli` | `cli_gap` |
| `implementation-memory` | 3 | `retain` | `repo_path:implementations/memory` | `non_cli_entrypoint` |
| `implementation-operations` | 8 | `retain` | `repo_path:implementations/operations` | `platform_path` |
| `implementation-package-boundaries` | 2 | `retain` | `repo_path:implementations` | `non_cli_entrypoint` |
| `implementation-perception` | 21 | `retain` | `repo_path:implementations/perception` | `dynamic_path` |
| `implementation-runtime` | 3 | `retain` | `repo_path:implementations/runtime` | `platform_path` |
| `implementation-vehicle` | 9 | `retain` | `repo_path:implementations/vehicle` | `platform_path` |

## Non-claims

- The record does not claim that unreached code is dead.
- Coverage percentages are not authorization for any disposition.
- `expose`, `retain`, and `remove` are candidates; no product change is
  performed by this review unit.
