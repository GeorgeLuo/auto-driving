// Decision region: proposal, plan, and action for the displayed frame.
// Classic script: the page's files share one global scope.
"use strict";

function renderDecision() {
  var cycle = currentPayload("decision");
  var frame = currentFrame();
  if (!cycle) {
    setText("decisionSummary", "no cycle");
    setText("decisionFrameIdentity", frame ? frame.frame_id : null);
    setText("decisionStatus", null);
    setText("decisionSelected", null);
    setText("decisionProposed", null);
    setText("decisionAuthority", null);
    setText("decisionReason", null);
    elements.decisionSource.textContent = "No decision cycle yet.";
    elements.decisionCandidates.textContent = "No decision cycle yet.";
    return;
  }
  var plan = cycle.plan || {};
  var authority = cycle.authority || {};
  var proposed = authority.proposed || {};
  var selected = plan.selected_proposal_id || "none";
  var candidates = Array.isArray(plan.candidates) ? plan.candidates : [];
  setText("decisionSummary", text(cycle.status, "unknown") + " · " + selected);
  setText("decisionFrameIdentity", frame ? frame.frame_id : cycle.frame_id);
  setText("decisionStatus", text(cycle.status, "unknown") + " / " + text(plan.status, "no plan"));
  setText("decisionSelected", selected);
  setText("decisionProposed", proposed && Object.keys(proposed).length
    ? "steering=" + text(proposed.steering, "—") + " throttle=" + text(proposed.throttle, "—")
    : "none");
  setText("decisionAuthority", typeof authority.proposed_applied === "boolean"
    ? text(authority.gate_id, "gate") + " · proposed_applied=" + authority.proposed_applied + " · Host delivery: absent" : "unavailable");
  setText("decisionReason", cycle.reason || (plan.reason || "—"));
  var sourceRefs = [];
  candidates.forEach(function (candidate) {
    (candidate.source_refs || []).forEach(function (source) { sourceRefs.push(source); });
  });
  elements.decisionSource.textContent = sourceRefs.length
    ? JSON.stringify(sourceRefs, null, 2) : "No source references.";
  elements.decisionCandidates.textContent = candidates.length
    ? candidates.map(function (candidate) {
      return [
        text(candidate.plugin_id, "unknown"),
        "lifecycle=" + text(candidate.lifecycle, "—"),
        "reason=" + text(candidate.reason, "—"),
        "command=" + JSON.stringify(candidate.command || null)
      ].join("\n");
    }).join("\n\n") : "No candidate proposals.";
}
