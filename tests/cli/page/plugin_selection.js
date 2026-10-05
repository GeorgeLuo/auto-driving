const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const snapshot = JSON.parse(fs.readFileSync(0, "utf8"));
function node() {
  return {
    children: [], attributes: {}, events: {}, _textContent: "",
    get textContent() { return this._textContent; },
    set textContent(value) { this._textContent = value; this.children = []; },
    appendChild(child) { this.children.push(child); },
    setAttribute(key, value) { this.attributes[key] = value; },
    getAttribute(key) { return this.attributes[key]; },
    addEventListener(name, handler) { this.events[name] = handler; },
    querySelectorAll(selector) {
      const inputs = this.children.flatMap((child) => child.children || [])
        .filter((child) => child.type === "checkbox");
      return selector.endsWith(":checked") ? inputs.filter((input) => input.checked) : inputs;
    }
  };
}
const elements = new Proxy({}, { get: (target, id) => target[id] || (target[id] = node()) });
const sent = [];
const ctx = {
  state: snapshot, elements, actionInFlight: false,
  text: (value) => String(value),
  setText(id, value) { elements[id].textContent = String(value); },
  document: { createElement: node },
  action(name, payload) { ctx.actionInFlight = true; sent.push(payload); }
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("cli/automa_cli/workbench_page/js/plugins.js", "utf8"), ctx);
const inputs = (step) => elements[ctx.pluginPanels[step].catalogId].querySelectorAll("input[data-plugin-id]");
const checked = (step) => inputs(step).filter((input) => input.checked).map((input) => input.getAttribute("data-plugin-id"));
function toggle(step, id, selected) {
  const input = inputs(step).find((input) => input.getAttribute("data-plugin-id") === id);
  input.checked = selected;
  input.events.change();
}
function accept(step, ids, preset) {
  ctx.state["active_" + step + "_plugin_ids"] = ids;
  ctx.state.machine_detail.pipeline[step + "_preset"] = preset;
  ctx.actionInFlight = false;
  ctx.settlePluginDraft(step);
  ctx.renderPlugins();
}

ctx.renderPlugins();
const mode = process.argv[2];
if (mode === "order") {
  const original = snapshot.active_perception_plugin_ids.slice();
  toggle("perception", "frame", true);
  assert.deepEqual(Array.from(sent.at(-1).active_plugin_ids), [...original, "frame"],
    "adding a plugin retains the CLI preset's execution order and appends it");
  accept("perception", [...original, "frame"], "custom");
  toggle("perception", "frame", false);
  assert.deepEqual(Array.from(sent.at(-1).active_plugin_ids), original,
    "removing the added plugin restores the original ordered selection");
  accept("perception", original, "multi_obstruction");
  // Pending toggles also retain the latest draft's order, rather than DOM order.
  toggle("perception", "frame", true);
  toggle("perception", "floor_plane", true);
  toggle("perception", "frame", false);
  assert.deepEqual(Array.from(ctx.pluginPanels.perception.queued), [...original, "floor_plane"]);
  ctx.revertPluginDraft("perception");
  ctx.actionInFlight = false;
  ctx.renderPlugins();
  assert.deepEqual(checked("perception").sort(), original.slice().sort());
  // The same function handles ordered memory plugins; the packaged catalog has one today.
  const panel = ctx.pluginPanels.memory;
  ctx.state.memory_plugin_catalog = {
    digest: "two-memory-plugins",
    plugins: [{ id: "first" }, { id: "second" }]
  };
  accept("memory", ["second"], "custom");
  toggle("memory", "first", true);
  assert.deepEqual(Array.from(sent.at(-1).active_plugin_ids), ["second", "first"]);
  assert.equal(panel.queued, null);
} else if (mode === "state") {
  for (const step of ["perception", "memory"]) {
    const originalInputs = inputs(step);
    const original = ctx.state["active_" + step + "_plugin_ids"].slice();
    ctx.state["active_" + step + "_plugin_ids"] = [];
    ctx.state.machine_detail.pipeline[step + "_preset"] = "custom";
    ctx.renderPlugins();
    assert.deepEqual(checked(step), [], "an API selection updates the browser's checkboxes");
    assert.strictEqual(inputs(step)[0], originalInputs[0], "polling preserves checkbox nodes");
    assert.match(elements[ctx.pluginPanels[step].summaryId].textContent, /0 active/);
    ctx.state["active_" + step + "_plugin_ids"] = original;
    ctx.renderPlugins();
    assert.deepEqual(checked(step).sort(), original.slice().sort());
  }
  toggle("perception", "frame", true);
  ctx.renderPlugins();
  assert(checked("perception").includes("frame"), "a poll keeps an in-flight local draft");
  assert.deepEqual(Array.from(ctx.pluginPanels.perception.draft),
    [...snapshot.active_perception_plugin_ids, "frame"]);
} else if (mode === "visibility") {
  for (const step of ["perception", "memory"]) {
    const panel = ctx.pluginPanels[step];
    assert(elements[panel.summaryId].textContent.includes(snapshot.machine_detail.pipeline[step + "_preset"]),
      "each step's preset is visible beside its controls");
    assert.equal(elements[step + "PluginOrder"].textContent,
      "Run order: " + snapshot["active_" + step + "_plugin_ids"].join(" → "));
    toggle(step, snapshot["active_" + step + "_plugin_ids"][0], false);
    assert(elements[panel.summaryId].textContent.includes("selection pending"));
    ctx.revertPluginDraft(step);
    ctx.actionInFlight = false;
    ctx.renderPlugins();
  }
  accept("memory", [], "custom");
  assert.equal(elements.memoryPluginOrder.textContent, "No memory plugins selected.");
} else {
  throw new Error("unknown test mode " + mode);
}
console.log("plugin selection " + mode + " ok");
