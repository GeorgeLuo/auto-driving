# Synthesis

This directory preserves research and technical observations that may inform
future work. A synthesis note connects an external idea to this repository,
identifies what is already supported, and defines the smallest experiment that
could validate its relevance.

Synthesis notes are not architecture contracts or accepted designs. Keep
derived historical reports out of this tree; raw captures belong with the
code that still produces them.

## Status

- `candidate`: relevant enough to retain, but not validated in this project.
- `evaluating`: a bounded experiment is currently producing evidence.
- `adopted`: validated behavior has moved into a milestone or reference
  contract; the note links to its destination.
- `rejected`: evidence did not justify adoption; the result is retained to
  avoid repeating the same investigation.

## Index

| Note | Status | Area | Candidate application |
| --- | --- | --- | --- |
| [Locality and length generalization in visual reasoning](locality-and-length-generalization.md) | candidate | Perception | Coarse global context, selective local inspection, and bounded sequential state |
| [Quantitative change analysis — v0 pseudocode](quantitative-change-analysis-pseudocode.md) | candidate | Engineering workflow research | Live `python3 -m qca analyze` / `diff` measurements of the current tree |
| [PiRacer physical perception strategies](piracer-physical-perception-strategies.md) | rejected | Physical perception | Floor-continuity did not improve two material behavioral measures on labeled physical frames |

## Note Shape

Each note should contain:

1. Source and status.
2. The relevant claim, separated from project-specific inference.
3. Applicable elements and related repository surfaces.
4. A bounded experiment and measurable adoption gate.
5. Constraints, non-goals, and conditions that justify revisiting it.

Keep settled behavior in `docs/reference/`.
