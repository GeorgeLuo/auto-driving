# Quantitative change analysis prototype

This package is the standalone, observations-only experiment for issue #180.
It is intentionally Python-first and standard-library-only.  It does not
assign a quality score, change proposal workflow state, or create a blocking
check.

From the repository root:

```sh
python3 -m qca analyze .
python3 -m qca analyze --ref HEAD
python3 -m qca diff --base <base-ref> --head <head-ref>
python3 -m qca diff --base <base-ref> --head <head-ref> --json report.json --markdown report.md
python3 -m qca backtest --manifest qca/backtests/m008.json
```

Use `--include-root` repeatedly to bound core measurements to an ownership
area.  The source inventory still classifies files outside those roots so
changed-file attribution remains visible.  JSON is the stable machine-facing
record; Markdown is a compact operator-facing rendering.  Diff
`review_targets` are deterministic inspection prompts for agents and humans,
not findings that should be accepted without review.  Rename detection is
deliberately disabled in v0: a move is represented as an old-file deletion and
a new-file addition until a stable callable-matching rule is justified.
