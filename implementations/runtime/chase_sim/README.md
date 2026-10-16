# Chase camera cadence

The runtime uses Metrics UI's `play_camera_stream_subscribe` when the loaded
game advertises `protocol.cameraStream.supported`. It requests 640×480 JPEG
frames at quality 0.8, with a maximum rate of 30 Hz and `latest-frame` delivery.
This is a requested upper bound; simulation and browser work determine the
actual source rate.

The receiver keeps one undecoded frame. Perception decodes that frame in memory
and runs sequentially; captures arriving during a decision replace the single
pending sample. Slow processing therefore skips older samples instead of
accumulating a queue. Perception overlays use the completed decision's matching
image and frame identity.

Subscription startup validates the passive preservation receipt. Each frame
validates actor, camera, epoch, advancing frame index, timestamps, image MIME,
dimensions, and raster contents. A correlated state request on the subscription
socket checks the scenario and control source against a state-derived startup
baseline before perception. Its deadline includes writing and waiting for the
reply; stop interrupts either. Session changes, stream failures, and malformed
data end the run. Stop and restart close the subscription and discard prior-session
captures. Subscribing does not acquire vehicle control.
An idle stream checks playback after the capture timeout: app/game pauses keep
the subscription open, while a playing game that produces no frames fails.

Browser source timestamps use a different monotonic clock. The receiver anchors
the first source timestamp to the local subscribe request, the earliest possible
capture time. This conservatively retains subscription, wire, and pending-slot
delay in frame age; consuming a buffered frame does not make it fresh again.

A successful capability response without stream support selects atomic capture.
Capability query errors stop the run; an arbitrary error code never selects a
fallback. Observation status reports `camera_transport` with its selection
reason, plus `frames_received` and `frames_replaced` for the stream. Counters
and capture timings stay out of cycle metadata.

Older frontends use atomic captures. A valid atomic receipt removes repeated
state/debug preflights after the initial capability check; every later receipt
is still validated. Runtime atomic captures also decode in memory. File-based
CLI sensor capture retains its existing output contract.

The CLI copies perception and action results at up to 30 Hz, with adaptive
backoff after slow requests (up to a 100 ms gap). The perception
monitor owns its publication while the decision copier updates the action page,
preventing two writers from moving the perception page backward. Runtime JPEG
responses and recordings share a synchronized cache for each frame. Validated
stream JPEG bytes pass through without another encode.
