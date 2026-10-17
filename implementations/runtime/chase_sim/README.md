# Chase camera cadence

The runtime uses Metrics UI's `play_camera_stream_subscribe` when the loaded
game advertises `protocol.cameraStream.supported`. It requests 640×480 JPEG
frames at quality 0.8, with a maximum rate of 30 Hz and `latest-frame` delivery.
This is an upper bound; simulation and browser work determine source rate.

The receiver offers encoded captures directly into the shared runtime's one
waiting slot, beside one active decision. Incoming identity, timestamp, MIME
and image-header checks do not decode RGB pixels. Only the capture selected by
the decision worker is decoded and state-checked. Camera inspection can reuse
the incoming JPEG without decoding it. Replaced captures never build a backlog.

Subscription startup validates passive preservation. A correlated state request
on the subscription socket checks scenario and control source before a selected
capture enters perception. Deadlines include writing and waiting; stop interrupts
either. Identity, scenario or simulation-epoch changes end every mode. Manual
and observe-only preserve the operator's source; autonomy establishes its
baseline after authorized WS acquisition and rejects later authority changes.
Stop/restart discard captures from previous generations. Subscription itself
never acquires control. Idle app/game pauses keep the subscription open; a
playing game that stops producing frames fails after the capture timeout.

`captured_at_ms` estimates capture time on the runtime clock, including delivery
and slot delay in age. Stream time is anchored conservatively to subscription
start. Atomic capture uses request start when source time is unavailable;
repeated identities retain their first estimate. Response completion never
refreshes a capture's age.

Absent stream capability or an explicit unsupported capability command selects
atomic capture. Transport timeouts, malformed replies, arbitrary errors and
advertised-stream failures stop the run. Observation status names the selected
transport and reports only `frames_received` and `frames_dropped`: distinct
accepted captures and received captures replaced before processing. Counters
accumulate for the process, not each subscription. Unrequested simulator frames
and intentional halt/stop discards are excluded; atomic capture may have no
drops. Counters and capture timing diagnostics stay out of cycle metadata.

A valid atomic receipt avoids repeated state/debug preflights after initial
discovery; every later receipt is still validated. Atomic runtime capture is in
memory; file-based sensor capture retains its output contract. Encoded images
stay in the runtime handoff and cache, outside the vehicle sensor contract.

Perception and action copying run independently of recording export, each with
one publisher and a target of 30 Hz. Slow requests retain adaptive backoff (up
to a 100 ms gap). Both transaction routes support `ETag` / `If-None-Match`:
unchanged usable pairs return `304` without image bytes. Expiry is checked
before conditional responses, and action telemetry coverage still refreshes.
Unavailable/failed encodes return the same image fields as null with HTTP 503;
view availability recovers without ending automation. Older hosts use metadata
checks to avoid fetching unchanged images. Terminal streams share the view's
observation read. Recording and publication share one synchronized JPEG cache.
