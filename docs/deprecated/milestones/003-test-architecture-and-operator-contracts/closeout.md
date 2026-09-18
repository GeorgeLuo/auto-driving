# Milestone 003 Closeout: Test Architecture and Operator Contracts

Status: closed 2026-07-15

## Outcome

The repository now has one canonical test tree and runner organized by production
ownership. Stable autonomy values, decision flow, perception evidence, activation
documents, runtime failure behavior, concrete implementations, CLI commands,
cross-layer integration, and opt-in live systems have distinct test locations.
The former duplicate runner and compatibility paths are removed.

The default suite is deterministic and offline. Pull requests run that suite on
Python 3.11 and publish branch-aware coverage for owned code without turning the
aggregate percentage into a quality gate. Simulator and Pi checks are named
opt-ins with explicit prerequisites, side effects, timeout behavior, and
unavailable outcomes.

Representative CLI families now derive human and JSON output from the same
operation state. Human output prioritizes outcome, relevant state, and recovery;
machine output retains complete diagnostics. Normal operation does not write
logs or artifacts unless recording is explicitly requested.

## Durable Decisions

- Tests mirror the ownership boundary they protect. Cross-layer behavior belongs
  under `integration`, experimental algorithms under `lab`, and mutable external
  systems under `live`.
- `tests/run.py` is the sole repository runner. Topic-specific development uses
  ordinary `unittest` module selection rather than accumulating runner flags.
- The default suite must not require a browser, simulator, network, Pi, or
  recording directory. Live checks are explicit additions, not hidden skips
  presented as validation.
- Shared test support is limited to process execution, explicit fixture
  documents, and external executable doubles. It does not reimplement business
  behavior or create a second application API.
- `unittest` remains sufficient. The milestone addressed ownership, invariants,
  and operator semantics rather than changing frameworks for style.
- Generic vehicle and autonomy values reject non-finite input before it can
  normalize into actuation. Serialized snapshots detach mutable metadata from
  live runtime state.
- Decision stages remain synchronous, optional, ordered, and no-op friendly.
  Runtime engine replacement is transactional; step failure returns idle control
  and does not prevent a later recovery.
- Perception evidence owns finite confidence, local identity, and normalized
  geometry validation. Evidence remains a claim from a plugin, not world truth.
- Decision and perception activation readers apply equivalent document
  validation and return detached selected configuration.
- Named built-in perception activations preserve the selected algorithm but
  refresh implementation details from the current catalog at deployment.
  Explicit custom activations remain author-owned.
- Human and JSON forms should share a normalized semantic outcome without
  requiring a universal renderer. Introduce shared rendering machinery only
  after repeated concrete duplication warrants it.
- Coverage is informational. Named failure invariants and behavior-sensitive
  tests matter more than maximizing incidental execution.
- The first physical readiness boundary is a read-only autonomy-status request
  that requires a loaded engine, loaded perception, and Donkey `user` mode. It
  never commands movement or changes mode.

## Defects Exposed

- Non-finite generic control values could cross an unsafe normalization boundary.
- Runtime replacement and step failures did not fully preserve known-good idle
  behavior and inspectable component state.
- Malformed activation documents could fail later than their owning boundary or
  expose configuration still attached to retained source data.
- Background automation startup could report success before the worker and view
  were actually ready, while stale or crashed state lacked concise recovery.
- Simulator readiness and deployment dry runs mixed operator conclusions with
  low-level command noise or omitted side-effect intent.
- A physical redeploy preserved a removed perception mapper import even though
  the selected built-in algorithm was still valid.

## Validation

- The canonical suite discovers 145 tests. The flagless run reports 143 passes
  and two explicit live skips in 44.225 seconds on the development machine.
- The coverage-enabled run reports the same 143 passes and two skips in 52.850
  seconds. Owned-code coverage is 63.1% across 7,481 statements and 2,000
  branches; it remains informational.
- The Python 3.11 pull-request job passed the final implementation head in 1m28s.
- The opt-in Pi run reports 144 passes with only the unrequested Chase check
  skipped. The powered Pi reported `IdleAutonomyEngine`,
  `lightweight_observer`, and Donkey `user` mode.
- The Pi readiness test sent only read-only status requests. Deployment setup
  used the explicit core/autonomy release workflow; neither path commanded
  vehicle movement.
- Deterministic tests cover ready, missing-activation, non-manual, and unreachable
  Pi outcomes without requiring hardware in normal development or CI.

## Deferred Work

- Define bounded decision memory from current observations without promoting
  plugin-local track IDs or perception claims into durable world truth.
- Add semantic quality fixtures only when a perception or memory hypothesis
  states what correctness means; representation health is not task accuracy.
- Use coverage reports to locate risks during concrete work rather than setting
  a repository-wide threshold from the current baseline.
- Exercise the Chase and Pi live checks when their environments are relevant;
  their opt-in status means deterministic CI cannot prove external readiness.
- Keep motion-capable physical validation behind separately named operations
  with stronger setup and safety requirements.

## References

- Frozen [milestone plan](plan.html)
- [Documentation ledger](../completed.md)
- Final implementation PR: [#19](https://github.com/GeorgeLuo/auto-driving/pull/19)

