# Quantitative Change Analysis — v0 Pseudocode

## Record

- **Status:** candidate
- **Prepared:** 2026-09-04
- **Related issue:** [#180](https://github.com/GeorgeLuo/auto-driving/issues/180)
- **Scope:** exploratory pseudocode for a standalone analyzer; it does not
  change proposal, amendment, implementation, review, or CI behavior.

## Does issue #180 make sense?

Yes. The issue has a sound experimental boundary:

1. record reproducible observations about a tree or a base→head change;
2. keep qualitative interpretation visibly separate from those observations;
3. let the existing reviewer/operator workflow decide what the information
   means;
4. promote only repeatedly useful signals, and only later consider workflow
   integration.

The important narrowing is to treat the first analyzer as instrumentation, not
as a code-quality judge. The initial implementation should use a small core of
Python and Git measurements. Duplication, entropy, coverage enrichment,
language adapters, LLM interpretation, and CI publication should remain
separate optional stages until the core produces useful questions on real
repository history.

## v0 boundary

The first standalone run supports two modes:

- `analyze <tree>` for a reproducible snapshot; and
- `diff --base <ref> --head <ref>` for a base→candidate report.

The deterministic report contains source classification, size/churn,
changed-file and changed-function shape, decision burden, imports, public
symbols, and optional verification-artifact metadata. It contains facts and
deltas, not a quality score, pass/fail threshold, or recommendation.

Every record carries the analyzer/schema version, resolved revisions or path,
configuration and exclusion hashes, source classifications, and parser/tool
versions. Stable ordering and canonical serialization are part of
reproducibility.

## Commented pseudocode

The following is intentionally Python-shaped pseudocode. Names describe the
future ownership boundary; they are not an executable module or a commitment
to these exact APIs.

```python
# -----------------------------
# Stable record shapes
# -----------------------------

class AnalysisRequest:
    # Exactly one source mode is selected so a snapshot cannot be mistaken for
    # a revision comparison.
    mode: "tree" | "diff"
    tree_path: Path | None
    base_ref: str | None
    head_ref: str | None
    config: AnalyzerConfig
    coverage_json: Path | None
    enable_experimental: bool


class AnalyzerConfig:
    # Rules are data rather than scattered conditionals, so a report can carry
    # the exact exclusions and classifications that produced it.
    schema_version: str
    include_roots: list[str]
    excluded_globs: list[str]
    generated_markers: list[str]
    vendor_markers: list[str]
    lab_roots: list[str]
    architecture_zones: dict[str, str]


class Report:
    # This is a measurement record. It deliberately has no "quality" field.
    identity: ReportIdentity
    source_inventory: list[SourceFile]
    snapshot: SnapshotMetrics
    diff: DiffMetrics | None
    verification: VerificationMetrics | None
    experimental: ExperimentalMetrics | None
    observations: list[Observation]


class QualitativeRecord:
    # Interpretation is a different record so readers can distinguish model
    # language from facts recomputable from repository state.
    evidence_ids: list[str]
    interpretations: list[str]
    uncertainties: list[str]
    reviewer_questions: list[str]
    workflow_decision: None  # The analyzer never approves, rejects, or gates.


# -----------------------------
# Public standalone entrypoint
# -----------------------------

def run(request: AnalysisRequest) -> Report:
    # Canonicalize before doing work so equivalent invocations produce the same
    # configuration hash and stable report identity.
    config = canonicalize_config(request.config)
    tool_versions = collect_parser_and_tool_versions()

    # Resolve refs to immutable SHAs. A tree-only run records its canonical path
    # and does not invent a base revision.
    source = resolve_source(request, config)
    validate_source_mode(source, request)

    # Classification happens before metrics so generated, vendored, runtime,
    # and other excluded material cannot contaminate a baseline.
    inventory = classify_and_inventory(source, config)
    included = [item for item in inventory if item.include_in_core_metrics]

    # The same analyzer functions read both sides of a diff. This keeps a delta
    # from becoming a second, subtly different implementation of the metrics.
    head_snapshot = measure_snapshot(included, revision=source.head)
    base_snapshot = None
    git_delta = None
    if source.mode == "diff":
        base_inventory = classify_and_inventory(source.at_base, config)
        base_included = [
            item for item in base_inventory if item.include_in_core_metrics
        ]
        base_snapshot = measure_snapshot(base_included, revision=source.base)
        git_delta = read_git_diff(source.base, source.head, config)

    # Map changed line ranges to parsed callables where possible. Unmapped lines
    # remain visible as file-level change rather than being silently discarded.
    diff_metrics = derive_diff_metrics(
        base_snapshot=base_snapshot,
        head_snapshot=head_snapshot,
        git_delta=git_delta,
    )

    verification = None
    if request.coverage_json is not None:
        # Coverage is enrichment, not a target. A mismatched or malformed
        # artifact becomes "unavailable" with provenance, never a zero.
        verification = read_verified_coverage(
            request.coverage_json,
            expected_head=source.head,
            changed_lines=git_delta.changed_lines if git_delta else None,
        )

    experimental = None
    if request.enable_experimental:
        # Experimental readings are namespaced so they cannot accidentally look
        # like stable core metrics or become workflow checks by serialization.
        experimental = measure_experimental_readings(included, git_delta)

    observations = build_observations(
        inventory=inventory,
        head=head_snapshot,
        base=base_snapshot,
        diff=diff_metrics,
        verification=verification,
        experimental=experimental,
    )

    # Canonical sorting is part of the deterministic contract. Do not sort by
    # a "notable" score because v0 has no universal threshold or score.
    return Report(
        identity=ReportIdentity(
            analyzer_version=ANALYZER_VERSION,
            schema_version=config.schema_version,
            mode=source.mode,
            base_sha=source.base,
            head_sha=source.head,
            analyzed_path=source.path,
            config_hash=hash_canonical(config),
            exclusion_hash=hash_exclusions(config),
            tool_versions=tool_versions,
        ),
        source_inventory=sort_inventory(inventory),
        snapshot=head_snapshot,
        diff=diff_metrics,
        verification=verification,
        experimental=experimental,
        observations=sort_observations(observations),
    )


def classify_and_inventory(source, config) -> list[SourceFile]:
    # Precedence is explicit: a generated file under a lab directory is still
    # generated, and a vendored file is not treated as first-party code.
    files = enumerate_files(source, include_roots=config.include_roots)
    result = []
    for path in files:
        kind = classify_path_and_content(
            path,
            generated_markers=config.generated_markers,
            vendor_markers=config.vendor_markers,
            lab_roots=config.lab_roots,
        )
        result.append(
            SourceFile(
                path=relative_path(path, source),
                source_class=kind,
                include_in_core_metrics=(
                    kind in {"production", "tests", "tooling", "lab"}
                    and not matches_any(path, config.excluded_globs)
                ),
            )
        )
    return result


def measure_snapshot(files: list[SourceFile], revision) -> SnapshotMetrics:
    # v0 is Python-first. Other languages are reported as present but marked
    # unsupported rather than analyzed with a misleading Python parser.
    metrics = empty_snapshot_metrics()
    for file in files:
        if file.extension != ".py":
            metrics.unsupported_files.append(file.path)
            continue

        text = read_text_at_revision(file, revision)
        tokens = tokenize_python(text)
        tree = parse_python_ast(text)

        # Logical/effective LOC must have a documented definition and be kept
        # separate from raw line count; comments and blank lines are not silently
        # treated as implementation burden.
        metrics.add_loc(file, logical_loc(tokens), effective_loc(tokens))
        metrics.callables.extend(measure_callables(tree, file.path))
        metrics.import_edges.extend(read_import_edges(tree, file.path))
        metrics.public_symbols.extend(read_public_symbols(tree, file.path))

    metrics.decision_burden = sum(
        max(0, callable.cyclomatic_complexity - 1)
        for callable in metrics.callables
    )
    return normalize_snapshot_metrics(metrics)


def measure_callables(tree, path) -> list[CallableMetrics]:
    # Each callable retains its source range so a Git hunk can be associated with
    # a concrete function without pretending every changed line has semantics.
    result = []
    for node in walk_functions_and_methods(tree):
        result.append(
            CallableMetrics(
                path=path,
                qualified_name=qualified_name(node),
                start_line=node.lineno,
                end_line=node.end_lineno,
                logical_loc=logical_loc_for_node(node),
                cyclomatic_complexity=count_decisions(node) + 1,
                decision_count=count_decisions(node),
                max_nesting=max_nesting(node),
            )
        )
    return result


def derive_diff_metrics(base_snapshot, head_snapshot, git_delta):
    # Deltas are calculated by stable identity where possible. New/deleted
    # callables remain explicit; they are not forced into a false match.
    delta = subtract_snapshots(base_snapshot, head_snapshot)
    delta.added_lines = git_delta.added_lines
    delta.deleted_lines = git_delta.deleted_lines
    delta.churn = delta.added_lines + delta.deleted_lines
    delta.changed_files = stable_unique(git_delta.changed_files)
    delta.changed_functions = map_hunks_to_callables(
        git_delta.hunks,
        base_snapshot.callables,
        head_snapshot.callables,
    )
    delta.new_dependency_edges = set(head_snapshot.import_edges) - set(
        base_snapshot.import_edges
    )
    delta.public_surface = compare_public_symbols(
        base_snapshot.public_symbols,
        head_snapshot.public_symbols,
    )
    delta.decision_burden_delta = (
        head_snapshot.decision_burden - base_snapshot.decision_burden
    )
    return normalize_diff_metrics(delta)


def build_observations(inventory, head, base, diff, verification, experimental):
    # These are phrased as inspectable facts. A later layer may ask whether a
    # fact is expected, but this function must not call it good or bad.
    observations = []
    observations.append(observation("changed_files", diff.changed_files if diff else []))
    observations.append(observation("production_loc", head.production_loc))
    observations.append(observation("test_loc", head.test_loc))
    observations.append(observation("decision_burden", head.decision_burden))
    if diff is not None:
        observations.append(observation("churn", diff.churn))
        observations.append(observation("decision_burden_delta", diff.decision_burden_delta))
        observations.append(observation("new_dependency_edges", diff.new_dependency_edges))
        observations.append(observation("public_surface_delta", diff.public_surface))
    if verification is not None:
        observations.append(observation("verification_provenance", verification.provenance))
    if experimental is not None:
        observations.append(observation("experimental", experimental))
    return observations


# -----------------------------
# Optional qualitative layer
# -----------------------------

def interpret(report, context_at_head, prior_report=None) -> QualitativeRecord:
    # Context must be limited to what existed at the analyzed head. Supplying
    # later review outcomes would turn a prospective backtest into hindsight.
    prompt = make_interpretation_prompt(
        proposal_or_amendment=context_at_head.proposal,
        implementation_diff=context_at_head.diff,
        deterministic_report=report,
        prior_report=prior_report,
    )
    raw = call_qualitative_analyzer(prompt)

    # The schema forces every interpretation to point back to observations and
    # makes uncertainty/questions first-class. The model cannot emit a workflow
    # action, severity, approval, rejection, or gate.
    return validate_qualitative_record(
        raw,
        allowed_evidence_ids={item.id for item in report.observations},
        require_uncertainty=True,
        force_workflow_decision_none=True,
    )


# -----------------------------
# Historical M008 backtest
# -----------------------------

def run_backtest(state_specs):
    # State specs are selected from history and frozen before analysis begins;
    # this is an experiment record, not a permanent workflow concept.
    prospective = []
    for state in state_specs:
        report = run(
            AnalysisRequest(
                mode="diff",
                base_ref=state.parent_sha,
                head_ref=state.head_sha,
                config=state.config,
                coverage_json=state.coverage_artifact_available_at_head,
                enable_experimental=False,
            )
        )
        # Only proposal, implementation, and review context available at this
        # historical state may be supplied to the qualitative layer.
        qualitative = interpret(
            report,
            context_at_head=load_context_as_of(state.head_sha),
        )
        prospective.append(
            BacktestRow(
                state=state,
                deterministic=report,
                qualitative=qualitative,
                future_history=None,
            )
        )

    # Reveal later outcomes only after the prospective records are sealed. The
    # operator then labels useful questions, noise, redundancy, and hindsight.
    later_history = load_subsequent_history(state_specs)
    return compare_prospective_readings_to_later_history(prospective, later_history)


# -----------------------------
# Deliberately deferred integration
# -----------------------------

def publish_advisory_report(report, destination):
    # This stage is allowed only after calibration. It writes an artifact and a
    # human-readable summary, but analyzer/tool failure remains non-blocking.
    write_json(report, destination / "quantitative-change-analysis.json")
    write_markdown_summary(report, destination / "quantitative-change-analysis.md")
    return "advisory-artifact-only"


def promote_signal(signal, calibration_log):
    # Generic code-shape anomalies remain advisory. Only a repeatedly useful,
    # machine-expressible repository invariant may later become a required check,
    # and that requires a separate workflow proposal.
    if signal.is_semantic_invariant and calibration_log.repeatedly_actionable(signal):
        return "candidate-for-separate-workflow-proposal"
    return "keep-advisory-or-remove"
```

## First implementation slice

The pseudocode intentionally leaves the first real implementation smaller than
the issue's complete metric inventory:

1. Python `ast`/`tokenize` snapshot metrics;
2. Git base→head hunks and changed-file mapping;
3. LOC/churn, callable size, cyclomatic complexity, decision burden, imports,
   and public-symbol deltas;
4. deterministic JSON plus a concise Markdown rendering;
5. fixtures for source classification, stable ordering, decision burden, and
   unchanged-input reproducibility.

Do not add duplication, entropy/surprisal, coverage, an LLM, CI annotations, or
workflow checks until this slice has been run on a tree and several historical
M008 transitions.

## Prototype checkpoint — 2026-09-04

The first executable slice is now available under [`qca/`](../../qca/) and is
being evaluated as a standalone experiment.  It exposes three documented
commands:

```sh
python3 -m qca analyze .
python3 -m qca diff --base <ref> --head <ref>
python3 -m qca backtest --manifest qca/backtests/m008.json
```

The M008 manifest samples seven immutable transitions labelled small, medium,
or large.  The committed [JSON report](artifacts/m008-qca-backtest.json) is the
machine-readable record and the [Markdown report](artifacts/m008-qca-backtest.md)
is the operator-facing summary.  The run completed for all seven states with
analyzer version `0.2.0`; the focused QCA suite covers classification, include
root behavior, Git diff mapping, reproducibility, and manifest output.

The first readings support a bounded hypothesis rather than a quality score:

- the narrow plugin-selection amendment, closeout, and cumulative-merge states
  have little or no callable/dependency/public-surface signal, so their
  semantically important documentation changes are visible primarily through
  file/class churn;
- the medium proposal states expose test/tooling/documentation spread and, in
  the plugin-selection proposal, four additional decision-burden units and
  seven import edges;
- the large workbench implementation state exposes a +583 decision-burden
  delta across 24 changed files, 101 new import edges, and 54 public-symbol
  additions, giving a reviewer concrete places to inspect instead of only a
  large line count;
- the acceptance state is structurally distinct from implementation: most of
  its 2,750 added lines are classified as documentation/evidence, while its
  code-facing delta is comparatively small.

Each diff also emits deterministic `review_targets` for changed callables,
new import edges, public-surface changes, and head syntax errors.  These are
inspection prompts for an operator or agent, not defect labels or gates.  The
backtest adds evidence-linked operator questions while keeping qualitative
interpretation and workflow decisions outside the analyzer.

The run also exposed an implementation constraint: excluded files must remain
classified for change attribution, but must not be tokenized as core metrics.
The prototype now preserves that distinction, which keeps the full seven-state
backtest repeatable while retaining documentation/evidence visibility.
For now, Git renames are represented explicitly as a deletion plus an addition;
stable moved-callable matching remains an open experiment question.

### Research-pass synthesis

Two independent passes compared the same immutable revisions with the same
analyzer output.  Both confirmed the gradient and byte-level reproducibility,
and both identified the same first-order repairs: align scoped paths, separate
all-file churn from configured core churn, make rename/deletion handling
explicit, and avoid presenting every standard-library import or simple added
callable as a separate alert.  The orchestrator applied those repairs and
restarted the run.

The resulting report is more useful as a handoff because it pairs:

1. raw observations and source-class attribution for the operator;
2. historical outcomes revealed only after each reading; and
3. deterministic review targets/questions that an agent can turn into an
   inspection plan.

The passes also establish a boundary for this experiment: the Python-first
signals do not explain M008's browser-visible interaction discoveries by
themselves.  That is a reason to evaluate an eventual qualitative/browser
adapter separately, not a reason to turn static measurements into a quality
gate.  No workflow, proposal, amendment, CI, or blocking behavior changes in
this PR.

## Open decisions made explicit by the pseudocode

- What exactly counts as logical versus effective LOC?
- Which ownership-zone map is authoritative for dependency observations?
- How are renamed files and moved callables matched?
- What comparable history defines a local baseline, if any?
- Which coverage artifact provenance is sufficient to enrich a report?
- How much proposal/review context can the qualitative layer consume without
  leaking later outcomes?
- Where should experimental reports live so they remain inspectable without
  becoming workflow state?

These are experiment questions, not hidden defaults. The implementation should
record unresolved choices in the report configuration and decision log.

## Adoption gate

Keep #180 in the standalone experiment until the M008 backtest shows that the
readings are deterministic, interpretable, and capable of producing concrete
reviewer questions that would plausibly have improved visibility. Remove noisy
or redundant metrics rather than promoting the entire initial list. A later
workflow proposal is justified only after that isolated usefulness gate passes.
