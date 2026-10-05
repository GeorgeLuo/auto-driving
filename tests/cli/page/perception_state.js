const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const elements = {};
["perceptionSummary", "perceptionPluginRuns", "perceptionCounts", "perceptionLines"].forEach((id) => {
  elements[id] = { textContent: "", title: "stale plugin title" };
});
const ctx = {
  elements,
  state: { current_frame: null, steps: { perception: null } },
  currentPayload() { return ctx.state.steps.perception; },
  currentFrame() { return ctx.state.current_frame; },
  text(value) { return value == null ? "—" : String(value); },
  setText(id, value) { elements[id].textContent = value; }
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("cli/automa_cli/workbench_page/js/perception.js", "utf8"), ctx);

ctx.renderPerception();
assert.equal(elements.perceptionSummary.textContent, "no output");
assert.equal(elements.perceptionLines.textContent, "—");
assert.equal(elements.perceptionSummary.title, "");

ctx.state.current_frame = { frame_id: "dropout", absent: true, absence_reason: "camera missing" };
ctx.renderPerception();
assert.equal(elements.perceptionSummary.textContent, "absent");
assert.equal(elements.perceptionLines.textContent, "camera missing");
assert.equal(elements.perceptionPluginRuns.textContent, null);

ctx.state.current_frame = { frame_id: "camera", absent: false };
ctx.state.steps.perception = {
  status: "ok", plugin_runs: [{ plugin_id: "frame" }], things: [], signals: [], lines: ["frame output"]
};
ctx.renderPerception();
assert.equal(elements.perceptionSummary.textContent, "ok");
assert.equal(elements.perceptionSummary.title, "frame");
assert.equal(elements.perceptionLines.textContent, "frame output");
console.log("perception state ok");
