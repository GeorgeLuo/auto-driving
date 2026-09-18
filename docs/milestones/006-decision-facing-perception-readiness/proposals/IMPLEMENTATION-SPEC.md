# D2 implementation — standalone inspector

This replaces the former live publication specification following the operator's
2026-09-10 direction on PR #203.

| Owner | Responsibility |
| --- | --- |
| `cli/automa_cli/decision_inspector.py` | Load saved sequence; run left/right scenarios; expose CLI result and dedicated loopback page |
| `cli/automa_cli/decision_view.html` | Render one selected artifact; preserve disclosure state |
| `cli/automa_cli/app.py` | `vehicles decision inspect` arguments and dispatch |
| `cli/automa_cli/decision.py` | Existing decoders and engine configuration; info command points to inspector |
| `examples/decision-inspection/` | Explicit synthetic saved input and reproducible operator command |

## Input and execution

Input is an existing `automa_decision_apply_sequence_v0` file or a directory
containing `sequence.json`. Reuse apply's frame/observation/memory validation
and its sequence bounds. Select one frame by zero-based position.

Use packaged shadow-proposals defaults, or a staged activation when `--id` is
provided. For each side, copy memory and reposition all supported image-relative
obstruction records. Clear their geometry, preserve all other inputs, and invoke
the existing `create_shadow_proposals_engine(config).run_cycle`.

Return `automa_decision_inspection_v1`: input filename/hash/frame identity,
engine/configuration, and left/right artifacts containing steering direction,
changed record IDs, and the complete cycle.

## CLI and HTTP

`vehicles decision inspect --from-run <path>` serves until Ctrl-C.
`--open` opens the printed URL. `--json` returns artifacts without serving.
`--frame N`, `--id <vehicle>`, and `--port N` select frame/configuration/port.

The standalone loopback server serves the page at `/` and immutable computed
JSON at `/api/inspection`. It has no perception routes, capture, polling,
generation matching, wall-clock expiry, or worker health dependency.

The page fetches the artifacts once and switches between them. Existing details
elements remain mounted, so toggling changes their content without closing them.
Input loading and decision execution remain server-side.

## Removed implementation

Remove the former D2 publisher and preview API from the perception server,
its automation attachment, and its fixture-specific tests/runner. Existing
live decision stream publication predates D2 and remains unchanged.

## Verification

1. Public CLI sample JSON yields left → steer right and right → steer left.
2. Dedicated server returns the same JSON as the CLI.
3. Chromium toggles update the same black-box card and preserve opened details.
4. Refresh succeeds without live runtime state; malformed/missing inputs fail
   before starting a server.
5. Existing decision commands, shadow surfaces, perception view, and memory
   publication checks remain green.

No new test scaffolding or live vehicle execution is part of this change.
