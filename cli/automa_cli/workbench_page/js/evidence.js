// Evidence region: perception output for the displayed frame.
// Classic script: the page's files share one global scope.
"use strict";

function renderEvidence() {
  var perception = currentPayload("perception");
  if (!perception) {
    setText("evidenceSummary", "no output");
    setText("pluginRuns", null);
    setText("evidenceCounts", null);
    elements.evidenceText.textContent = "No perception output yet.";
    return;
  }
  var pluginRunIds = Array.isArray(perception.plugin_runs)
    ? perception.plugin_runs.map(function (run) { return run.plugin_id; })
    : [];
  setText("evidenceSummary", text(perception.status));
  elements.evidenceSummary.title = pluginRunIds.length ? pluginRunIds.join(", ") : "";
  setText("pluginRuns", Array.isArray(perception.plugin_runs) ? perception.plugin_runs.length : 0);
  setText("evidenceCounts", (perception.things || []).length + " / " + (perception.signals || []).length);
  elements.evidenceText.textContent = Array.isArray(perception.lines)
    ? perception.lines.join("\n") : "Structured perception output has no text lines.";
}
