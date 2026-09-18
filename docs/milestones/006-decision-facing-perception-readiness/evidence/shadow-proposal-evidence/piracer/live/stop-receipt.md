# PiRacer live-view stop receipt

- Command: `PIRACER_BASE_URL=http://192.168.0.168:8887 ./cli/automa vehicles decision live --id piracer --port 0 --timeout-s 8`
- Local generation: `bcb0f79426f89a5c6a90541f46dae3cdbfb2fe325a4e02cf8b936c814699bee9`
- Stop result: `Live decision view stopped.`
- Vehicle state during the final accepted interval: `user` mode; user, pilot,
  and host-selected output all zero.
- Setup action: one operator-authorized low-throttle user-mode pulse, followed
  immediately by an explicit zero-output stop. The pulse is outside the final
  stationary interval and is not represented as host telemetry for that interval.

This stops only the local read-only view server after the explicit vehicle
stop; it does not change the PiRacer runtime or drive mode.
