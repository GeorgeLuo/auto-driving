# Proposal: Chase capture image-envelope closure

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Chase capture image-envelope closure |
| Proposal branch | `m007/chase-capture-image-envelope-proposal` |
| Implementation branch | `m007/chase-capture-image-envelope` |
| Exit criterion | M007-03 |
| Review finding | [P2] reject mismatched image dimensions and encoding at the Chase boundary ([PR #81 inline finding](https://github.com/GeorgeLuo/auto-driving/pull/81#discussion_r3849733269)) |
| Review kind | Review repair |

## Review Question

Does the Chase adapter reject every sensor image whose decoded dimensions or
raster format disagree with the declared image envelope, and reject mismatched
data-URL MIME or declared content type, while preserving valid raster captures
and the independent optional evaluator-reference contract?

This is a new product review unit because PR #81 is a closed-plan cumulative
review surface. The image-envelope finding must be repaired and reviewed at
its owning Chase adapter boundary; PR #81 remains historical and is not edited
by this unit.

The proposal is grounded in the exact Phase C finding. At the reviewed PR #81
head, `validate_chase_sensor_capture` checks only positive declared dimensions
and whether Pillow can decode some image bytes. It accepts a 1x1 PNG declared
as 640x480, accepts PNG bytes under `contentType=text/plain`, and accepts a
`data:text/plain;base64,...` PNG while reporting separately declared
`image/png`. The accepted M007-03 invalid-dimensions/encoding matrix row is
therefore still open.

## Proposed Contract

### Authoritative image envelope

The Chase adapter's sensor validator is the one enforcement boundary for the
image envelope. A capture is valid only when all of the following agree:

1. `sensor.image.width` and `sensor.image.height` are positive JSON integers
   and equal the dimensions decoded from the raster bytes.
2. `sensor.image.dataUrl` is a non-empty data URL with a supported raster image
   MIME, a decodable payload, and a MIME whose raster format agrees with the
   decoded Pillow format. Text, SVG, missing, malformed, or non-raster MIME
   values fail closed as `capture_image_invalid`.
3. When `sensor.image.contentType` is a non-empty string, it is a canonical
   MIME equal to the data-URL MIME and to the decoded raster format. When the
   key is absent, or its value is an empty/whitespace string, there is no
   declared content type, but the validator still returns the validated
   data-URL MIME as canonical `content_type`; omitting the input declaration
   does not weaken format or dimension checks. When the key is present with
   any other non-string value, including `null`, the declaration is invalid
   rather than silently treated as absent.
   Media-type comparisons are case-insensitive, and canonical MIME means the
   lower-case bare media type from the explicit supported mapping. Data-URL
   parameters are transport metadata and are not copied into `content_type`;
   a declared `contentType` must contain only that bare MIME. An absent or
   empty/whitespace `contentType` contributes no declaration, while a
   non-string value or a declared MIME parameter is invalid rather than
   silently treated as absent.
4. Validation and the write path share one validated image envelope:
   `_capture_front_camera` must write bytes whose decoded dimensions and raster
   format are the values validated at this boundary, and must record the
   canonical `content_type` returned by that validation. It may not independently
   select a MIME from the raw data URL or `contentType`, or let a later
   `_image_content_type`/`_decode_data_url` fallback override the validated
   result. If the implementation re-decodes because of an existing API shape,
   it must use strict decoding and verify the bytes and decoded metadata before
   writing.
5. Existing required capture identity and passive-playback checks remain
   unchanged. Optional evaluator-reference validation remains independent:
   a valid sensor image without `evaluator.reference` is still an available
   sensor capture with an unavailable optional reference.

The supported raster mapping is explicit and narrow: PNG ↔ `image/png`, JPEG
↔ `image/jpeg`, GIF ↔ `image/gif`, and WEBP ↔ `image/webp`. This is the complete
supported set for this unit. The implementation may not preserve a broader
Pillow-accepted set or accept an unrecognized MIME merely because Pillow
decodes the bytes; adding another format requires a separately reviewed
contract.

### Failure and consumer behavior

- Every mismatch raises `ChaseCaptureValidationError` with code
  `capture_image_invalid`, a stable image-envelope path, and no generic
  `ValueError` escape.
- Validation runs before the Chase worker writes bytes to its requested output
  path. Invalid dimensions, format, data-URL MIME, or declared content type
  cannot publish a frame, construct a sensor snapshot, or become a perception
  input.
- Valid raster captures preserve the existing frame identity, width/height,
  optional evaluator-reference behavior, and worker output behavior. This unit
  does not make evaluator data required for sensor-only perception.

## Ownership

| Boundary | Owner in this unit |
| --- | --- |
| Capture image schema validation, MIME/format mapping, decoded dimensions, and canonical output | `implementations/vehicle/chase_sim/frame_identity.py` |
| Capture-to-file consumer and structured propagation of validation failure | `implementations/vehicle/chase_sim/car.py`, only where required to consume the validated bytes/metadata before writing; no new enforcement owner |
| Regression proof | `tests/implementations/vehicle/test_chase_frame_identity.py` and affected Chase capture tests |

No separately owned external repository or live simulator capability is needed
to close this deterministic Chase adapter defect. The Metrics UI remains an
untrusted producer at this boundary; its response envelope must satisfy the
local contract before any bytes are written.

## Affected Paths

- `implementations/vehicle/chase_sim/frame_identity.py` for one authoritative
  decode, dimension, MIME, and raster-format validation path.
- `implementations/vehicle/chase_sim/car.py` only if the existing write path
  must consume validated decoded metadata or preserve the structured error.
- `tests/implementations/vehicle/test_chase_frame_identity.py` and narrowly
  related Chase capture tests for direct mismatch, valid-capture, and
  no-write regressions.

This proposal PR does not contain those later implementation files.

## Adversarial Matrix

| Attempted bypass | Required response |
| --- | --- |
| 1x1 PNG declared as 640x480 | Reject with `capture_image_invalid`; decoded and declared dimensions must agree |
| Positive declared dimensions that differ in only width or only height | Same rejection; do not validate only the total pixel count |
| PNG bytes with `contentType=text/plain` | Reject the declared content-type mismatch |
| PNG bytes in `data:text/plain;base64,...` while `contentType=image/png` | Reject the data-URL MIME mismatch; do not trust the separate declaration |
| PNG bytes in `data:image/jpeg;base64,...` | Reject the data-URL MIME versus decoded raster-format mismatch |
| JPEG bytes in `data:image/png;base64,...` | Reject the inverse raster-format/MIME mismatch |
| Matching data-URL MIME but conflicting non-empty `contentType` | Reject; both declarations must identify the same canonical raster MIME |
| Present non-string (including `null`) `contentType` or a declared MIME with parameters | Reject as an invalid declaration; do not treat malformed metadata as omission |
| Decodable BMP/TIFF (or another unlisted raster) under an unrecognized MIME | Reject; Pillow decoding alone does not expand the supported mapping |
| Missing or empty `contentType` with a matching supported raster data URL | Accept and return the validated data-URL MIME as canonical content type |
| Valid PNG with matching dimensions, data-URL MIME, and content type | Accept and preserve frame identity and sensor-only operation |
| Valid image with evaluator reference omitted | Accept sensor capture; report evaluator reference unavailable as before |
| Malformed base64, empty payload, undecodable bytes, text MIME, or SVG-only image | Reject with `capture_image_invalid` before write/publish |
| Invalid image envelope reaching `_capture_front_camera` | No output file or sensor snapshot is published; retain the structured capture error |
| Validated metadata disagrees with raw image data at the write path | Reject before write; never let a second decode or raw MIME declaration replace the validator's canonical dimensions, format, bytes, or content type |
| Valid image with unrelated malformed evaluator reference | Preserve sensor validity and report evaluator-reference invalidity through its existing optional path |

## External Assumptions

- Metrics UI supplies a data URL and, when present, a `contentType` describing
  the same raster image bytes; those fields are untrusted and may disagree.
- Pillow's decoded `format` and `size` are authoritative for bytes already
  received by this local adapter. The implementation must not infer dimensions
  or format solely from MIME or filename.
- The worker's existing raster output path can continue to write validated
  bytes without changing the observation-only authority or frame identity
  contract.
- The four-format MIME mapping named in this proposal is sufficient for the
  current worker output; adding a new format requires a later scoped contract
  rather than silently broadening this repair.
- Deterministic fixtures are sufficient to close this finding. No live
  simulator, browser, or evidence recapture is required; existing live
  acceptance artifacts remain historical to their recorded identities.

## Non-Goals

- Editing or repairing cumulative PR #81, its closeout packet, or the
  completed-milestone ledger.
- Changing the frame-identity schema, frame-index strictness, passive playback
  semantics, evaluator-reference authority, or observation-only control policy.
- Making evaluator reference data required for sensor-only perception or
  changing evaluator scoring behavior.
- Adding support for SVG, arbitrary MIME types, or a new image codec without a
  separately reviewed contract.
- Redesigning the Metrics UI response schema, network transport, retry policy,
  image quality, resizing, color management, or worker output naming.
- Catching unrelated runtime exceptions or moving enforcement to a generic
  process boundary.
- Combining this proposal with implementation in the same PR, or adopting a
  non-frontier combined-repair workflow.
- Repairing the separate PR #81 PiRacer perception-inspection finding.
- Beginning implementation in this proposal PR.

## File Impact

| Path | Proposal change | Later implementation role |
| --- | --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/chase-capture-image-envelope.md` | Add this reviewed contract | Immutable accepted proposal |
| `docs/milestones/007-cli-operator-usability/plan.md` | Select the current frontier and record M007-03 ownership; proposal workflow forbids pre-claiming criterion or risk changes | Record proposal/implementation handoffs only |
| `docs/milestones/007-cli-operator-usability/plan.html` | Generated rendering of the plan transition | Regenerated with canonical plan changes |
| `implementations/vehicle/chase_sim/frame_identity.py` | None | Authoritative decoded dimension/format/MIME/content-type validation |
| `implementations/vehicle/chase_sim/car.py` | None | Consume validated bytes/metadata and preserve structured failures |
| `tests/implementations/vehicle/test_chase_frame_identity.py` | None | Direct mismatch, valid, optional-reference, and no-write regressions |

## Validation Plan

### Proposal PR

The proposal PR must contain only this artifact, the canonical plan transition,
and generated plan HTML:

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 -m unittest \
  tests.docs.test_milestone_proposal_workflow \
  tests.docs.test_milestone_planning
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/chase-capture-image-envelope-proposal \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

The proposal review verifies the exact PR #81 finding link, one review
question, the `frame_identity.py` owner, M007-03 routing, the dimension/
format/MIME/content-type matrix, evaluator-reference independence, and the
absence of implementation files.

### Implementation PR after proposal acceptance

Deterministic tests must:

- exercise each direct mismatch in the matrix with a real decodable raster
  fixture and assert `capture_image_invalid` rather than a generic exception;
- verify decoded width and height are each compared with the declaration and
  that decoded raster format, data-URL MIME, and declared content type share
  one canonical mapping, including case normalization and malformed
  non-string/parameterized declarations;
- verify valid supported raster captures with and without optional evaluator
  reference preserve the current sensor result and canonical content type;
- patch the Chase write path to prove invalid captures are rejected before any
  output file or sensor snapshot is published, and that the path writes only
  the validated bytes with the validator's canonical content type; and
- preserve existing malformed-byte, SVG-only, frame-identity, and optional
  evaluator-reference regressions.

Run the focused Chase frame-identity tests, the affected vehicle suite, the
repository suite, workflow validation, Markdown rendering check, and
`git diff --check`. No live simulator or browser run is required for this
deterministic review repair.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Chase capture image-envelope closure in PR #{pr}: frame_identity.py rejects decoded dimension, raster-format, data-URL MIME, and declared content-type mismatches before write/publish with direct regressions, while valid sensor captures and optional evaluator-reference behavior remain intact.",
  "criterion_updates": {
    "M007-03": {
      "status": "Met",
      "evidence": "PR #{pr} closes the Phase C invalid-dimensions/encoding row at the Chase adapter boundary: decoded dimensions and raster format agree with the data-URL MIME and any present content-type declaration, invalid captures fail as capture_image_invalid before publication, and valid reference-less sensor capture remains available."
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "The Chase image-envelope repair is promoted and the milestone remains idle; no further Phase C product unit is contracted by this handoff.",
    "revisit_when": "A later proposal is justified by a remaining finding or a new milestone acceptance decision."
  }
}
```

This handoff applies only after the implementation review has verified the
entire matrix and the exact-head acceptance receipt. It does not mark M007-06
Met or authorize cumulative PR #81 to merge.

## Sequence After This Proposal Merges

1. Obtain the exact-head proposal review receipt and merge this proposal into
   `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal` for the proposal PR and confirm
   `ready_for_implementation` with the recorded reviewed head and merge commit.
3. Start `m007/chase-capture-image-envelope` and implement only this contract.
   Parked product work from the former combined PR may be rebased onto that
   branch after acceptance.
4. Review the implementation against the matrix, repair within this unit if
   required, then complete the implementation handoff.
5. Return M007 to idle. This unit does not select a successor.

## Review Kind

**Review repair** — a separate owned product review unit is required because
the exact P2 was found during the rejected cumulative PR #81 review and the
closed-plan PR must remain unchanged. The unit is bounded to the Chase
image-envelope owner and its direct regressions.
