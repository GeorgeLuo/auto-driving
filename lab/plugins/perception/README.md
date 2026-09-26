# Perception Candidates

Every candidate receives normalized front-camera images and emits the stable
`PerceptionText` shape through the generic worker. Candidate dependencies run
inside a candidate-local virtual environment so experiments do not expand the
core runtime requirements.

Use `automa vehicles perception candidates` for readiness and
`automa vehicles perception compare <image-dir>` for a no-write comparison of
all ready candidates. Add `--record` only when review artifacts are needed.

Plugins are stateless between frames. Keep previous images, track identities,
smoothing, and other temporal history in `inputs.memory`, under plugin-owned
keys. Configuration and reusable model resources can remain on the instance.
`state_mode` describes the temporal input horizon (`stateless`, `pairwise`, or
`windowed`); it does not permit private history. Set `memory_required=True` when
the algorithm requires the shared map, and implement `reset(memory)` to remove
only that plugin's keys. Missing required inputs invoke this reset hook.
Plugins own history bounds and successful/warm-up/error commit decisions.

Hosts supply one map for a sequence and clear or replace it for a new run.
The isolated worker round-trips that map on each call, so restarting a worker
retains continuity when the caller supplies the same map. Its private transport
uses Python serialization for arrays and plugin records; it is a trusted local
process protocol, not a recording or remote-input format. This copies history
per call; keep histories bounded. In-process execution has no transport cost.

A plugin must produce equivalent evidence and next memory when recreated between
frames. Catalog conformance tests also check empty-map restart and interleaved
runs on one instance. Add a deterministic fixture to those tests with each new
plugin; external models may be stubbed while their adapters are exercised.
