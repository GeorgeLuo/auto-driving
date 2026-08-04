# Live CLI session runner (human-in-the-loop)

Evidence tooling for M007 live operator sessions. The runner executes a YAML
catalog of steps, captures machine output, prompts for human visual judgment
and notes, and writes a structured session artifact that reviewers or agents
can consume.

It does **not** change Automa product behavior. Product fixes still land in
separately authorized PRs.

## Roles

| Role | Responsibility |
| --- | --- |
| Runner | Exact commands, transcripts, JSON captures, digests, cleanup-oriented steps |
| Human | Visual pass/fail, free-text notes, optional findings |
| Agent / reviewer | Read `result.json` + `findings.jsonl` and propose next work |

## Quick start

From the auto-driving repository root:

```sh
# List bundled catalogs
python3 docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py \
  --list-catalogs

# Dry-run acceptance catalog (no commands executed; no human prompts)
python3 docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py \
  --catalog docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/m007-acceptance.yaml \
  --dry-run \
  --session-dir /tmp/live-cli-acceptance-dry

# Interactive acceptance session (Chrome Metrics UI must already be up)
python3 docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py \
  --catalog docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/m007-acceptance.yaml \
  --metrics-ui-origin http://localhost:5050 \
  --metrics-ui-repo /path/to/Stream-Metrics-UI \
  --browser-name Chrome \
  --browser-version "…paste from chrome://version…" \
  --operator "your-name" \
  --browser-view /path/to/cropped-browser-view.png \
  --session-dir docs/milestones/007-cli-operator-usability/evidence/live-cli-acceptance/session-$(date +%Y%m%d-%H%M%S)
```

Exploratory discovery (does not alone produce an M007-05 pass):

```sh
python3 docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py \
  --catalog docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/exploratory-discovery.yaml \
  --metrics-ui-origin http://localhost:5050
```

Dependency: **PyYAML** for `.yaml` catalogs (`pip install pyyaml` if needed).

## Catalogs

| File | Track | Purpose |
| --- | --- | --- |
| `catalogs/m007-acceptance.yaml` | `acceptance` | Contracted Chase 6-step journey + help audit |
| `catalogs/exploratory-discovery.yaml` | `exploratory` | Capture-once multi-engine / compare / memory check |

### Catalog schema (`live_cli_session_catalog_v0`)

Top-level fields:

- `id`, `track` (`acceptance` \| `exploratory`), `title`, `description`
- `vehicle_id`, `metrics_ui_origin`, `perception_algorithm`
- `gates` — required gate ids for acceptance verdicts
- `steps[]` — ordered operator steps

Each step may include:

- `id`, `kind` (`baseline` \| `command`), `question`, `safety`
- `commands` — argv arrays relative to repo root
- `primary_cue` — what the human should look for in CLI/browser
- `visual_required` / `visual_prompt`
- `gate_ids`, `required_for_verdict`
- `expect_exit`, `allow_nonzero_exit`
- `capture_json` — extra JSON status capture
- `capture_view_latest` — fetch loopback `/api/latest` after running JSON status
- `requires_prompt` — ask the operator for a path/variable (e.g. recorded `src_dir`)

## Session artifact layout

```text
session-dir/
  baseline.json            # operator, browser, repos, session_visible, precondition
  catalog.json
  catalog-source.txt
  result.json              # live_cli_session_result_v0 (no self-digest)
  digests.json             # detached hashes of all final files except itself
  findings.json
  findings.jsonl           # one finding object per line for agents
  human-notes.md
  transcripts/cli-transcript.txt   # ordered commands with started/ended timestamps
  pre-baseline-status.json         # worker/deployment/view before baseline
  precondition-cleanup.json        # recorded if an earlier worker was stopped
  session-fingerprint-baseline.json
  session-fingerprint-latest.json
  session-fingerprint-cleanup.json
  initial-status.json / running-status.json / stopped-status.json / cleanup-status.json
  view-publication.json    # pure JSON /api/latest (schema automa_perception_publication_v1)
  browser-view.png         # bound only after view_correlation health floor
  browser-view-meta.json   # source mtime/sha, floor, redacted import path
  auto-driving-worktree.diff  # when auto-driving worktree is dirty
  steps/<step-id>/
    envelope.json          # commands include started_at_utc / ended_at_utc
    cmd-00.stdout.txt
    cmd-00.stderr.txt
  steps/_precondition_cleanup/   # when needed
  steps/_cleanup/                # final stop + status
```

### Result verdict rules

**Acceptance catalogs:**

| Result | When |
| --- | --- |
| `pass` | Every required gate is `pass`; no acceptance blockers |
| `findings` | A required gate fails, or a blocking finding is recorded |
| `incomplete` | A required gate was skipped or never evaluated |

**Exploratory catalogs:** `complete`, `findings`, or `incomplete` (never an M007-05 `pass`).

### Finding records (`live_cli_session_finding_v0`)

```json
{
  "schema": "live_cli_session_finding_v0",
  "id": "M007-LIVE-001",
  "track": "exploratory",
  "step_id": "compare-candidates",
  "classification": "usability_defect",
  "severity": "P2",
  "summary": "…",
  "human_notes": "…",
  "evidence": ["steps/compare-candidates/envelope.json"],
  "repro": ["./cli/automa vehicles perception compare …"]
}
```

Agents should prefer `findings.jsonl` + `result.json` over re-parsing multi-megabyte transcripts.

## Human prompt contract

For each step the runner prints the primary cue, then asks:

1. Visual / step result: pass / fail / skip
2. Optional notes
3. Optional finding (severity + summary)

Rules of thumb (from the live acceptance discussion):

- Do not make the operator parse raw JSON to decide pass/fail.
- Visual inspection of the Automa view is required for acceptance startup.
- A record path alone is never the sole human success signal.

## Relation to PR #88

| Concern | Owner |
| --- | --- |
| Session runner implementation | This tool / this PR |
| Filled `evidence/live-cli-acceptance/result.json` for M007-05 | Evidence PR after a completed HITL session |
| Product CLI repairs (compare JSON dumps, FastSAM readiness, apply id collisions) | Separate proposal/implementation PRs |

Recommended flow:

1. Review and land the runner in isolation.
2. Run `m007-acceptance` interactively with Chrome up.
3. Commit the session directory (or copy fields into the formal evidence scaffold) on the evidence PR.
4. Use exploratory `findings.jsonl` to author the next proposal.

## Acceptance pass constraints

An acceptance catalog can return `pass` only when all of the following hold:

- execution mode is **interactive live** (not `--dry-run`, not `--non-interactive`)
- named `--operator` is recorded
- required machine validators pass on captured status/view JSON
- interactive human visual confirmation was recorded
- `--browser-name`, `--browser-version`, and `--metrics-ui-repo` are provided
- dirty worktrees include `diff_identity` (tracked patch + untracked content hashes)
  and `auto-driving-worktree.diff` when auto-driving is dirty
- baseline records `session_visible` protected fields from the initial fingerprint
- **precondition cleanup**: any pre-existing running worker is stopped and recorded
  before the acceptance baseline; initial layers require worker not running
- **preservation**: each JSON capture binds its own fingerprint (including failed
  extraction as `None`); within a receipt, all six fields match before/after;
  across commands, stable projection compares game/scenario/epoch, control
  source/input, and playback mode (`phase`/`pendingAction`) — natural
  `frameIndex` advancement is allowed; cleanup status is also preservation-checked
- **view identity**: `/api/latest` must use `automa_perception_publication_v1` and
  the expected vehicle id
- **browser-view.png** is bound only after `view_correlation` establishes the
  health floor; source mtime must postdate that floor (preserved on copy); import
  paths are redacted
- **cleanup** proves every observed worker PID is dead (not only the final status PID)

Dry-run and non-interactive sessions are capture helpers only; they resolve to
`incomplete` for acceptance catalogs even if every auto-visual is `pass`.

## Non-interactive mode

For tests and CI smoke:

```sh
python3 …/session_runner.py --catalog …/m007-acceptance.yaml --dry-run --non-interactive --auto-visual skip
```

`--non-interactive` without `--dry-run` will execute real commands and auto-apply `--auto-visual` (default `skip`), which makes acceptance catalogs resolve to `incomplete` unless every visual gate is auto-passed deliberately.
