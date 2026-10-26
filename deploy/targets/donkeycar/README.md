# DonkeyCar Deployment Bundle

This directory contains source that is synced to the physical PiRacer runtime.

## Local To Remote Mapping

- `deploy/targets/donkeycar/app/` -> `piracer:/home/piracer/mycar/`
- generated `deploy/targets/donkeycar/vendor/donkeycar/` -> `piracer:/home/piracer/projects/donkeycar/`
- `deploy/targets/donkeycar/systemd/` -> a rendered and enabled `automa-donkey.service`

Use the CLI for deployment:

```sh
./cli/automa vehicles discover --provider picar --url http://piracer.local:8887
./cli/automa vehicles status
./cli/automa vehicles create host --id piracer --provider picar --url http://piracer.local:8887
./cli/automa vehicles update host --id piracer --restart
```

Creation installs the core harness and boot service, deploys the controller
release, restarts it and verifies every step in manual mode. Later updates use
the recorded SSH connection. `vehicles delete host --id piracer` disables and
stops the service and removes local host data; the harness stays on the Pi.

Movement uses the same commands as Chase after deployment:

```sh
./cli/automa vehicles automation run --id piracer --num-decisions 10
./cli/automa vehicles automation stop --id piracer
./cli/automa vehicles automation restart --id piracer --observe-only
```

These commands use `/host/runtime` to operate the shared host. Restart
stops execution and restarts the systemd-managed process before starting a new
run. The service still boots in manual mode. The CLI's local monitor publishes
the onboard frames and decisions; it never calculates a second control policy.

The CLI prepares the generated DonkeyCar vendor checkout from
`donkeycar-vendor.json` and `patches/` before syncing. The generated checkout is
ignored by git and should not be edited directly.

Runtime data is intentionally excluded from sync:

- `deploy/targets/donkeycar/app/data/`
- `deploy/targets/donkeycar/app/logs/`
- `*.pid`

Persisted runtime arguments live at `/home/piracer/.config/automa/donkey.env`.
They are changed only by an explicit `--restart --drive-args=...` update and are
not part of the app sync.

Core sync also preserves `autonomy/`, `implementations/`, and `runtime/` in the
remote app. `vehicles update host` installs those packages as a hashed,
versioned controller release and writes explicit perception/decision activation
manifests under `/home/piracer/mycar/runtime/`.

The remote path is still named `mycar` because that is the DonkeyCar app
convention. The local path uses `app` so the top-level project is not organized
around one specific DonkeyCar instance.

## DonkeyCar Vendor Source

The DonkeyCar framework source is generated locally:

- manifest: `deploy/targets/donkeycar/donkeycar-vendor.json`
- local patch: `deploy/targets/donkeycar/patches/waveshare-donkeycar-local.patch`
- generated checkout: `deploy/targets/donkeycar/vendor/donkeycar/`

`vehicles create host` creates or verifies that checkout automatically.
