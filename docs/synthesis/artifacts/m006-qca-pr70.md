# QCA 0.3 on M006 cumulative PR 70

Analyzer `0.3.0`. In-progress milestone head
`6da43547d16195ccc70b4804b8229cc5d2bed057`
(`milestone/006-decision-facing-perception-readiness`).
Current frontier (cross-environment shadow proposal evidence) is
`ready_for_proposal` and is **not** in this head.

Reproduce:

```sh
python3 -m qca backtest --manifest qca/backtests/m006-pr70.json \
  --markdown /tmp/m006-qca.md
```

## Gradient

| State | What | Files | Core churn | Decision burden Δ | Python +/− |
| --- | --- | ---: | ---: | ---: | ---: |
| `foundation-impl` | PR 74: DecisionData, proposals, mixer, authority | 15 | 4292 | +382 | +4292 / 0 |
| `surfaces-impl` | PR 80: stage/info/apply/stream/view | 14 | 5796 | +471 | +5460 / −63 |
| `cumulative-pr70` | PR 70 three-dot vs merge-base with `main` | 31 | 10103 | +853 | +9772 / −64 |

Cumulative production Python is **+5,912 / −63**. `cli/automa_cli/decision.py` is about half of that (**+3,036 / −56**). Tests add **+3,855**. Docs/proposals add ~2.6k and are not in core Python metrics. `decision_view.html` is +27 production lines and is **not parsed** as Python. Browser/UI is `not_measured`.

## Cumulative factor dump (changed-file findings)

| Factor | Findings | Notes |
| --- | ---: | --- |
| contracts | 453 | inventory of public callables/CLI |
| patterns | 364 | 337 `raise`; 19 broad except; 8 swallowed |
| coupling | 159 | 130 unresolved/external imports, mostly stdlib |
| functional_style | 74 | 50 mutating calls, 24 attribute writes |
| redundancy | 14 | clone groups |
| lifecycle | 12 | name matches |
| functionality | 1 | `shadow_runner.__call__` Protocol stub |
| test / e2e / ui | 0 | e2e and UI not measured |

`duplicate_ast_loc` delta for the whole PR is **+19** (extra cloned statements), not 1,077.

Foundation (PR 74) is the more value-shaped core: **9** functional-style hits vs **162** on M008’s workbench implementation. Surfaces (PR 80) look more like CLI/state plumbing: `decision.py` +3k, 12 clone groups, 63 functional-style hits.

Clone leads that are actually copies (not `to_dict` / timestamp inventory): `_is_json_primitive`; CLI info/stream/update handlers; `_manifest_get_str`/`_dict`; `_int_or_none`; `_pid_alive`/`_process_alive`; `_emit` ×6; catalog `available_*_ids`; `_load_state`/`default_load_latest`.

## Compared with M008

| | M008 total onto `main` | M006 PR 70 |
| --- | ---: | ---: |
| Files | 40 | 31 |
| Python net | +5,616 | +9,708 |
| Decision burden Δ | +593 | +853 |
| Changed-file findings | 831 | 1,077 |

M006 is a **larger Python add** than M008. Most of the extra is Automa decision CLI (`decision.py`) plus tests, not the proposal plugin (`avoid_recent_obstruction` is 396 lines).

This is a reading of the open cumulative PR. It is not a review of the unmet evidence frontier (M006-06/07).
