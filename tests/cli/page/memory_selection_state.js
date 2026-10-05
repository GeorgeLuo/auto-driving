const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const elements = {};
["memoryRecords", "memoryHealth", "memoryEpoch", "memoryCount", "memoryPolicy",
 "memoryDrops", "memorySelection", "memorySelected", "memorySearch"].forEach((id) => {
  elements[id] = {
    textContent: "", hidden: false, open: false, children: [],
    getAttribute() { return "true"; },
    addEventListener() {},
    appendChild(child) { this.children.push(child); }
  };
});
const ctx = {
  elements,
  state: { phase: "paused", active_memory_plugin_ids: [] },
  currentPayload() { return null; },
  setText(id, value) { elements[id].textContent = value; },
  document: { createElement() { return {}; } }
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("cli/automa_cli/workbench_page/js/memory.js", "utf8"), ctx);

ctx.renderMemory();
assert.equal(elements.memoryHealth.textContent, "disabled");
assert.equal(elements.memoryRecords.children.at(-1).textContent, "No memory plugins selected.");

ctx.state.active_memory_plugin_ids = ["bounded_evidence"];
ctx.renderMemory();
assert.equal(elements.memoryHealth.textContent, "no frame yet");

ctx.state.phase = "failed";
ctx.state.failure_boundary = "memory";
ctx.state.active_memory_plugin_ids = [];
ctx.renderMemory();
assert.equal(elements.memoryHealth.textContent, "update failed");
assert.equal(elements.memoryRecords.children.at(-1).textContent,
  "Memory update stopped this replay. See Failure for details.");
console.log("memory selection state ok");
