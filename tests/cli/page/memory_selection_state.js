const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

function fakeElement() {
  return {
    hidden: false, open: false, value: "", className: "", children: [], attributes: {},
    _text: "", listeners: {},
    get textContent() { return this._text; },
    set textContent(value) {
      this._text = String(value);
      if (value === "") this.children = [];
    },
    getAttribute(name) { return this.attributes[name]; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
    closest(selector) {
      return selector === "button[data-plugin-id]" && this.attributes["data-plugin-id"] ? this : null;
    },
    contains(node) { return node === this || this.children.includes(node); },
    focus() { ctx.document.activeElement = this; },
    appendChild(child) { this.children.push(child); },
    querySelector() { return this.children[0] || null; },
    querySelectorAll() { return this.children.filter((child) => child.attributes["data-record-id"]); },
    classList: { toggle() {} }
  };
}

const elements = {};
["memoryRecords", "memoryHealth", "memoryPlugins", "memoryPublisher", "memoryEpoch",
 "memoryCount", "memoryPolicy", "memoryDrops", "memoryDropsSummary", "memoryDropsText",
 "memorySelection", "memorySelected", "memorySearch"].forEach((id) => {
  elements[id] = fakeElement();
});
const ctx = {
  elements,
  notice: "",
  state: { phase: "paused", active_memory_plugin_ids: [] },
  currentPayload(key) { return ctx.state.steps ? ctx.state.steps[key] : null; },
  text(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback || "—";
    return String(value);
  },
  setText(id, value, fallback) { elements[id].textContent = ctx.text(value, fallback); },
  setNotice(message) { ctx.notice = message; },
  document: { createElement() { return fakeElement(); } }
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("cli/automa_cli/workbench_page/js/memory.js", "utf8"), ctx);

ctx.renderMemory();
assert.equal(elements.memoryHealth.textContent, "disabled");
assert.equal(elements.memoryRecords.children.at(-1).textContent, "No memory plugins selected.");
assert.equal(elements.memoryPlugins.children.length, 0);

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

// Two plugins: both are listed, the publisher is named and shown by default.
function record(id) {
  return { record_id: id, kind: "floor_boundary", label: id, origin: {} };
}
function twoPluginReport(publisher) {
  return {
    schema: "memory_report_v1",
    plugins: [
      { plugin_id: "bounded_evidence", state: {
        health: "healthy", epoch_id: "epoch-b", record_count: 2,
        records: [record("thing:a"), record("thing:b")], metadata: { policy: "recency" } } },
      { plugin_id: "recording_test", state: {
        health: null, epoch_id: "epoch-1", record_count: 1, records: [record("rec-1")] } }
    ],
    evidence_publisher: publisher
  };
}
function pluginRows() {
  return elements.memoryPlugins.children.map((button) => ({
    id: button.attributes["data-plugin-id"],
    text: button.textContent,
    shown: button.attributes["aria-pressed"] === "true"
  }));
}
function recordIds() {
  return elements.memoryRecords.children
    .map((child) => child.attributes["data-record-id"])
    .filter(Boolean);
}

ctx.state = {
  phase: "paused",
  active_memory_plugin_ids: ["bounded_evidence", "recording_test"],
  steps: { memory: twoPluginReport("bounded_evidence") }
};
ctx.renderMemory();
assert.deepEqual(pluginRows(), [
  { id: "bounded_evidence", text: "bounded_evidence · 2 records · publisher", shown: true },
  { id: "recording_test", text: "recording_test · 1 records", shown: false }
]);
assert.equal(elements.memoryPublisher.textContent, "bounded_evidence");
assert.equal(elements.memoryHealth.textContent, "healthy");
assert.equal(elements.memoryEpoch.textContent, "epoch-b");
assert.equal(elements.memoryCount.textContent, "2");
assert.deepEqual(recordIds(), ["thing:a", "thing:b"]);

// Picking the other plugin shows its ledger; the publisher stays named.
// Enter and Space activate a native button through a click with detail 0.
elements.memoryPlugins.children[1].focus();
for (const handler of elements.memoryPlugins.listeners.click || []) {
  handler({ target: elements.memoryPlugins.children[1], detail: 0, stopPropagation() {} });
}
assert.equal(ctx.notice, "Showing memory plugin recording_test.");
assert.deepEqual(pluginRows().map((row) => row.shown), [false, true]);
assert.equal(elements.memoryPublisher.textContent, "bounded_evidence");
assert.equal(elements.memoryHealth.textContent, "no health");
assert.equal(elements.memoryEpoch.textContent, "epoch-1");
assert.deepEqual(recordIds(), ["rec-1"]);
assert.equal(ctx.document.activeElement.getAttribute("data-plugin-id"), "recording_test");

// The pick holds on the next frame while the report lists that plugin.
ctx.state.steps = { memory: twoPluginReport("bounded_evidence") };
ctx.state.steps.memory.plugins[1].state.record_count = 2;
ctx.state.steps.memory.plugins[1].state.records.push(record("rec-2"));
ctx.renderMemory();
assert.deepEqual(recordIds(), ["rec-1", "rec-2"]);
assert.equal(ctx.document.activeElement.getAttribute("data-plugin-id"), "recording_test");
assert(elements.memoryPlugins.children.includes(ctx.document.activeElement));

// With no publisher and no listed pick, no plugin is shown by position.
ctx.state.steps = { memory: {
  schema: "memory_report_v1",
  plugins: [
    { plugin_id: "alpha", state: { record_count: 0, records: [] } },
    { plugin_id: "beta", state: { record_count: 0, records: [] } }
  ],
  evidence_publisher: null
} };
ctx.renderMemory();
assert.equal(ctx.selectedMemoryPluginId, null);
assert.deepEqual(pluginRows().map((row) => row.shown), [false, false]);
assert.equal(elements.memoryPublisher.textContent, "none");
assert.equal(elements.memoryHealth.textContent, "pick a plugin");
assert.equal(elements.memoryRecords.children.at(-1).textContent,
  "No evidence publisher this frame. Pick a plugin to see its records.");
console.log("memory selection state ok");
