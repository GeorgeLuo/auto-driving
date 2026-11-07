# <Change title>

## Summary

<!-- What changed and why? Keep this scoped to the user-visible or maintainer-
     visible outcome. -->

## Validation

<!-- List the focused commands and their results, including any
     `python3 tests/run.py --systems <group> -k <pattern>` runs. CI runs the
     deterministic suite and the Python quality check; tests/README.md
     "Validating a change" says which systems tests a change needs. -->

- Python quality (`python3 -m scripts.validation.check`): <!-- Result for Python changes; otherwise explain why not applicable. -->
- QCA inspection: <!-- For Python changes, identify the PR summary or base/head inspected and give the location and disposition of each material candidate: fixed, retained with a code-based reason, or unresolved. If unavailable, say why. QCA is informational; a successful job does not establish that candidates were reviewed. -->

## Risks and follow-up

<!-- Note known limitations or concrete follow-up. Use "None" when there is
     nothing material to call out. -->
