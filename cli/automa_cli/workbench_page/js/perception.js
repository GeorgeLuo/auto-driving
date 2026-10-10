// Perception region: perception output for the displayed frame.
// Classic script: the page's files share one global scope.
"use strict";

function renderPerception() {
  var perception = currentPayload("perception");
  if (!perception) {
    var frame = currentFrame();
    var absent = frame && frame.absent;
    setText("perceptionSummary", absent ? "absent" : "no output");
    elements.perceptionSummary.title = "";
    setText("perceptionPluginRuns", null);
    setText("perceptionCounts", null);
    elements.perceptionLines.textContent = absent ? text(frame.absence_reason) : "—";
    return;
  }
  var pluginRunIds = Array.isArray(perception.plugin_runs)
    ? perception.plugin_runs.map(function (run) { return run.plugin_id; })
    : [];
  setText("perceptionSummary", text(perception.status));
  elements.perceptionSummary.title = pluginRunIds.length ? pluginRunIds.join(", ") : "";
  setText("perceptionPluginRuns", Array.isArray(perception.plugin_runs) ? perception.plugin_runs.length : 0);
  setText("perceptionCounts", (perception.things || []).length + " / " + (perception.signals || []).length);
  elements.perceptionLines.textContent = Array.isArray(perception.lines)
    ? perception.lines.join("\n") : "";
}
