# Session notes — m007-acceptance

- started_at_utc: `2026-08-05T23:47:54.250402Z`
- execution_mode: `interactive_live`
- track: `acceptance`
- operator: `gluo`

## baseline

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: origin=http://localhost:5050; auto_driving=caf335797b71df1323736a2054934b7c211418b0; worktree=clean

## help-top

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: (none)

## help-vehicles

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: (none)

## help-automation

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: (none)

## status-initial

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: initial_layers: initial layers healthy

## update-perception

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: staged_layers: staging left worker stopped with deployed perception

## automation-run

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: (none)

## status-running

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: view_latest: mode=bounded_stale derived_lag=15 bound=24: correlation proven; running_layers: running layers healthy; authority: observe_only / not_applied / recording=false; view_correlation: mode=bounded_stale derived_lag=15 bound=24: correlation proven; preservation: protected session fields preserved (stable projection)

## automation-stop

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: (none)

## status-stopped

- status: `pass`
- visual: `pass`
- notes: (none)
- machine: stopped_layers: stopped layers healthy; default_recording: no new automation run directories; preservation: protected session fields preserved (stable projection)

## Verdict

- result: `pass`
- reason: (none)
- findings: 0
- cleanup: {'attempted': True, 'needed': True, 'stop_exit_code': 0, 'final_status_exit_code': 0, 'worker_stopped': True, 'pid_alive': False, 'pids': [47071, 47195], 'pid_liveness': {'47071': False, '47195': False}, 'error': None, 'preservation': {'ok': True, 'summary': 'protected session fields preserved (stable projection)'}, 'stopped_layers_summary': 'stopped layers healthy', 'pid': 47195}
