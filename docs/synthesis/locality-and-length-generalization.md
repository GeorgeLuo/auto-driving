# Locality and Length Generalization in Visual Reasoning

## Record

- **Status:** candidate
- **Synthesized:** 2026-07-14
- **Area:** perception, temporal evaluation
- **Source:** [On Locality and Length Generalization in Visual Reasoning](https://arxiv.org/pdf/2607.09061)

## Relevant Finding

The paper finds that local visual observations combined with recurrent state can
generalize better than full-resolution global input on visual tasks that require
sequential state tracking. It also finds that global input remains effective for
recall and search tasks that do not require maintaining state.

The project-specific inference is not that all perception should become local.
Frame-local proposal methods and persistent scene understanding serve different
tasks and should be evaluated accordingly.

## Applicable Elements

| Element | Possible use here | Boundary |
| --- | --- | --- |
| Coarse peripheral view | Preserve full-frame spatial context at low cost | Does not itself establish object identity or depth |
| High-resolution local glimpses | Inspect a small number of proposed or uncertain regions | Begin with digital crops; do not couple this to vehicle motion |
| Bounded recurrent state | Retain recent evidence, unresolved regions, and inspection history | Plugin-local state is not durable decision memory |
| Visitation memory | Avoid repeatedly inspecting the same region while missing others | Store coordinates and timestamps rather than modifying source images |
| Length-generalization tests | Tune on short sequences and validate on longer or denser ones | Simulator truth belongs in evaluation, never plugin input |

## Existing Repository Support

- `autonomy/perception/plugin.py` already distinguishes stateless, pairwise, and
  windowed plugin state without prescribing perceived meanings.
- `implementations/perception/components/camera.py` supplies a normalized camera
  frame from which a coarse overview and source-resolution crops can be derived.
- `implementations/perception/motion/tracks.py` provides a stateful temporal
  baseline, although its groups are motion evidence rather than object identity.
- `cli/automa_cli/perception_evaluation.py` provides an evaluation boundary but
  currently emphasizes availability and adjacent-frame continuity more than
  long-horizon coverage or identity stability.

No stable autonomy contract needs to change to test this idea.

## Bounded Experiment

Create one isolated lab perception candidate and compare three configurations on
the same captured sequences:

1. Full-frame proposal baseline.
2. Low-resolution overview plus source-resolution local inspection.
3. The same local inspection with a bounded visitation ledger.

Use the `chaser depth obstacles` simulator scenario first. The candidate receives
only vehicle camera components; hidden simulator state may be used by the
evaluator to score results.

Measure:

- relevant-region coverage;
- missed relevant regions;
- identity switches or track discontinuities;
- redundant region visits;
- recovery after skipped frames or visual interruptions;
- latency and peak memory as sequence length increases.

Tune on short, sparse sequences and evaluate without retuning on longer sequences,
more simultaneous regions, frame gaps, and changed capture resolution.

## Adoption Gate

Promote the approach only if local inspection with bounded state improves
long-sequence coverage or temporal consistency over the full-frame baseline while
keeping latency and memory bounded. The result must hold across more than one
simulator layout before it changes implementation contracts or reaches the
physical vehicle runtime.

## Constraints

- Do not reproduce or train the paper's recurrent neural architecture initially.
- Do not assume results from static synthetic canvases transfer directly to an
  embodied, moving camera.
- Do not introduce a generic attention-action abstraction before the lab plugin
  demonstrates a need for one.
- Do not apply foveated processing to simple recall tasks when a full-frame method
  is more direct.

## Revisit When

- persistent region or object identity becomes active milestone work;
- full-frame perception exceeds the target processing budget;
- evaluation includes long sequences and controlled frame loss;
- an information-gathering action policy is being considered.
