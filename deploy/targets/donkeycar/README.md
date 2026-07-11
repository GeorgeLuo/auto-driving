# DonkeyCar Deployment Bundle

This directory contains source that is synced to the physical PiRacer runtime.

## Local To Remote Mapping

- `deploy/targets/donkeycar/app/` -> `piracer:/home/piracer/mycar/`
- generated `deploy/targets/donkeycar/vendor/donkeycar/` -> `piracer:/home/piracer/projects/donkeycar/`

Use the CLI for deployment:

```sh
./cli/automa vehicles update core --id piracer
./cli/automa vehicles update autonomy --id piracer --restart
```

The CLI prepares the generated DonkeyCar vendor checkout from
`donkeycar-vendor.json` and `patches/` before syncing. The generated checkout is
ignored by git and should not be edited directly.

Runtime data is intentionally excluded from sync:

- `deploy/targets/donkeycar/app/data/`
- `deploy/targets/donkeycar/app/logs/`
- `*.pid`

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
