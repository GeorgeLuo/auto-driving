# Milestone 001 Closeout: Automation Engine Foundation

Status: closed 2026-07-11

## Outcome

The repository now has a shared, vehicle-agnostic controller shape. Chase and
DonkeyCar both run `DecisionCycle` through `AutonomyCycleHost`; environment
adapters remain responsible for sensor capture and application of control.
The only shipped decision engine is `idle`, which returns zero steering and
throttle.

## Durable Decisions

- A `SensorSnapshot` and `Observation` are the stable inputs to later decision
  behavior.
- Perception converts sensor readings into evidence; it does not assert world
  truth or receive privileged simulator map state.
- Memory, patterns, and projections are explicit optional cycle stages. They
  remain unimplemented until a real use case defines their behavior.
- The Chase worker may take WS control but currently uses a stop-only safety
  gate. The Pi remains in Donkey `user` mode unless autonomy mode is explicitly
  requested.
- Physical releases are versioned and hashed. Core DonkeyCar deployment stays
  separate from autonomy release deployment.

## Validation

- The 52-test harness passes with 51 offline tests and one opt-in live-simulator
  test skipped by default.
- Physical deployment previously verified archive transfer, the idle engine,
  manual mode, and front-camera availability without commanding movement.
- CLI help, documentation links, directory contract JSON, and diff hygiene were
  checked during the documentation closeout.

## Deferred Work

- Implement real decision memory based on live `Observation` values.
- Add a non-idle engine only after memory and action-policy requirements are
  explicit.
- Consider richer cycle visualization only when current JSON and stream output
  are insufficient for debugging.

## References

- Baseline commit: `4bca440` (`Establish automation engine foundation`)
- Documentation and cleanup commit: `aa0327f` (`Align runtime documentation and
  remove synthetic memory`)
- Frozen [milestone plan](plan.html)
