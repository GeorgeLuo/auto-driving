# DonkeyCar Deployment Bundle

This directory contains source that is synced to the physical PiRacer runtime.

## Local To Remote Mapping

- `deploy/targets/donkeycar/app/` -> `piracer:/home/piracer/mycar/`
- generated `deploy/targets/donkeycar/vendor/donkeycar/` -> `piracer:/home/piracer/projects/donkeycar/`
- `deploy/targets/donkeycar/systemd/` -> a rendered and enabled `automa-donkey.service`

Use the CLI for deployment:

```sh
./cli/automa vehicles update core --id piracer
./cli/automa vehicles update autonomy --id piracer --restart
```

Core update installs the systemd service, starts it when inactive, and waits for
the read-only autonomy status endpoint in manual mode. Once installed, powering
on the Pi is sufficient to start the Donkey runtime. The service restarts an
unexpectedly exited process and sends output to the system journal rather than
an accumulating project log file.

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
remote app. `vehicles update autonomy` installs those packages as a hashed,
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

`vehicles update core` creates or verifies that checkout automatically.
