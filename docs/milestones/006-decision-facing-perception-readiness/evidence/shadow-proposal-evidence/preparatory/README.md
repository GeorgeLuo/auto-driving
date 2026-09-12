# Preparatory-only public-door check

This directory is not a Chase or PiRacer evidence package. It contains one
offline fixture result used to verify the existing staged decision input/output
boundary before live readiness was available.

The check staged `shadow-proposals` in a temporary isolated runtime and ran:

```sh
./cli/automa vehicles decision apply \
  --id chase-sim-chaser \
  --from-run tests/cli/decision/fixtures/apply_active_left \
  --json --record
```

The result was deterministic across two canonical passes. It is retained only
to demonstrate the public replay boundary and the distinction between the
nonzero proposal and shadow-only idle authority. It has no live run identity,
host interval, original source image, browser screenshot, D1 receipt, or D2
receipt; it cannot promote either M006 criterion.

The raw preparatory summary is [public-door-result.json](public-door-result.json).
