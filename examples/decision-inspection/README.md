# Decision inspector

This sample is one synthetic saved observation/memory input with an obstruction.
The inspector computes both side scenarios with the packaged proposal
composition. It does not capture images, start automation, or send vehicle commands.

From the repository root:

```sh
./cli/automa vehicles decision inspect --from-run examples/decision-inspection --open
```

Choose **Right obstruction** to see **steer left**, and **Left obstruction** to
see **steer right** in the same black-box card. Open details stay open on toggle.
The page's inputs and outputs come from the server's computed artifacts.

To print the same artifacts as JSON instead of serving the page:

```sh
./cli/automa vehicles decision inspect --from-run examples/decision-inspection --json
```

Supply any saved `automa_decision_apply_sequence_v1` file or directory containing
`sequence.json`. Use `--frame N` to select its zero-based frame position.
Use `--id <vehicle>` to read the vehicle's staged proposal, plan, and action
steps (the action must be `hold`). Plugins load from the controller bundle
recorded by each activation, as step info and the worker's proposal runner do;
that bundle must exist locally. Omit `--id` to use packaged defaults without
staging a vehicle. `--port N` selects a preferred local port. Ctrl-C stops the
inspector.

Both scenarios reposition all supported image-relative obstruction records in a
copy of the selected memory, clearing their original geometry. All other inputs,
including source timestamps, stay fixed. Stale or otherwise unusable evidence
may produce hold; the inspector does not manufacture a steering decision.

The live automation server publishes its running cycle's perception, memory,
and decision views. This offline inspector serves its own root page and has
no worker generation or expiry.
Reloading the page reloads the computed artifacts. Restart the command to load a
different input file or step selection.
