# Decision inspector

This sample is one synthetic saved observation/memory input with an obstruction.
The inspector computes both side scenarios with the packaged shadow decision
engine. It does not capture images, start automation, or send vehicle commands.

From the repository root:

```sh
./cli/automa vehicles decision inspect --from-run examples/decision-inspection --open
```

Choose **Right obstruction** to see **steer left**, and **Left obstruction** to
see **steer right** in the same black-box card. Open details stay open on toggle.
The page's inputs and outputs come from the server's computed artifacts.

To get the same artifacts without starting a server:

```sh
./cli/automa vehicles decision inspect --from-run examples/decision-inspection --json
```

Supply any saved `automa_decision_apply_sequence_v0` file or directory containing
`sequence.json`. Use `--frame N` to select its zero-based frame position.
Use `--id <vehicle>` to read a staged shadow engine configuration; omit it to
use packaged defaults without staging a vehicle. `--port N` selects a preferred
local port. Ctrl-C stops the inspector.

Both scenarios reposition all supported image-relative obstruction records in a
copy of the selected memory, clearing their original geometry. All other inputs,
including source timestamps, stay fixed. Stale or otherwise unusable evidence
may produce hold; the inspector does not manufacture a steering decision.

The live automation server remains a separate perception/memory surface. This
inspector serves its own root page and has no worker generation or expiry.
Reloading the page reloads the computed artifacts. Restart the command to load a
different input file or engine configuration.
