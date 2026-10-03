# Memory shared library

Plain code: functions, plus the working types they return. Code belongs here
when more than one memory plugin imports it.

## Topics

| Topic | Holds |
|---|---|
| `evidence_ledger/` | The bounded evidence ledger value (`ledger.py`) and the reduction of an observation into it (`reduction.py`). Each memory plugin keeps a ledger at its own key and publishes its records. |

## Rules

- Organize by topic: `shared/<topic>/<module>.py`. Add a new topic folder
  rather than nesting deeper.
- Code here is never a plugin and is never resolved by the framework.
- Add code here when a second plugin imports it, not before.
