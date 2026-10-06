// Runs the /memory and /perception page scripts against a small fake DOM and
// checks that each lists every memory plugin by plugin_id and names the
// evidence publisher.
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

function fakeElement() {
  return {
    children: [], dataset: {}, style: {}, attributes: {}, listeners: {},
    hidden: false, className: "", type: "", _text: "",
    get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); },
    set textContent(value) { this._text = String(value); this.children = []; },
    get classList() {
      const element = this;
      return {
        toggle(name, on) {
          const names = new Set(element.className.split(/\s+/).filter(Boolean));
          if (on ?? !names.has(name)) names.add(name); else names.delete(name);
          element.className = [...names].join(" ");
        },
        contains(name) { return element.className.split(/\s+/).includes(name); }
      };
    },
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this._text = ""; this.children = [...nodes]; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name]; },
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
    removeEventListener() {},
    descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); },
    querySelectorAll(selector) {
      const name = selector.replace(/^\./, "");
      return this.descendants().filter((element) => element.classList.contains(name));
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    closest(selector) { return this.classList.contains(selector.replace(/^\./, "")) ? this : null; },
    contains(node) { return node === this || this.descendants().includes(node); },
    getContext() { return new Proxy({}, { get: () => () => ({ width: 0 }) }); }
  };
}

function pageContext(fetchLatest) {
  const byId = new Map();
  const ctx = {
    console,
    window: { addEventListener() {} },
    setInterval() {},
    fetch: fetchLatest,
    document: {
      getElementById(id) {
        if (!byId.has(id)) byId.set(id, fakeElement());
        return byId.get(id);
      },
      createElement() {
        const element = fakeElement();
        element.focus = () => { ctx.document.activeElement = element; };
        return element;
      }
    }
  };
  vm.createContext(ctx);
  return ctx;
}

function runPage(path, ctx) {
  const html = fs.readFileSync(path, "utf8");
  const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
  vm.runInContext(script, ctx);
}

function metrics(element) {
  const pairs = {};
  for (let index = 0; index < element.children.length; index += 2) {
    pairs[element.children[index].textContent] = element.children[index + 1].textContent;
  }
  return pairs;
}

function record(id) {
  return { record_id: id, kind: "floor_boundary", label: id, confidence: 0.8, origin: {} };
}

const BOUNDED = {
  plugin_id: "bounded_evidence",
  state: {
    health: "healthy", epoch_id: "epoch-b", record_count: 2,
    bounds: { max_records: 32, max_age_ms: 5000 },
    records: [record("thing:a"), record("thing:b")]
  }
};
const RECORDING = {
  plugin_id: "recording_test",
  state: { health: null, epoch_id: "epoch-1", record_count: 1, records: [record("rec-1")] }
};

function publication(plugins, publisher) {
  return {
    schema: "automa_perception_publication_v2",
    vehicle_id: "piracer",
    frame: { frame_id: "frame_1" },
    perception: { things: [], plugin_runs: [] },
    memory: { schema: "memory_report_v1", plugins, evidence_publisher: publisher }
  };
}

async function settle() {
  for (let index = 0; index < 5; index += 1) await new Promise((resolve) => setImmediate(resolve));
}

async function memoryPage() {
  let latest = publication([BOUNDED], "bounded_evidence");
  const ctx = pageContext(async () => ({ ok: true, json: async () => latest }));
  runPage("cli/automa_cli/memory_view.html", ctx);
  await settle();
  const $ = (id) => ctx.document.getElementById(id);
  const pluginRows = () => $("pluginList").querySelectorAll(".plugin-row").map((button) => ({
    id: button.dataset.plugin,
    label: button.children[0].textContent,
    meta: button.children[2].textContent,
    shown: button.classList.contains("selected")
  }));
  const keys = () => $("keyList").querySelectorAll(".key-row").map((button) => button.dataset.key);

  // bounded_evidence alone: today's ledger plus the publisher.
  assert.deepEqual(pluginRows(), [{
    id: "bounded_evidence", label: "plugin · evidence publisher",
    meta: "healthy · epoch epoch-b · 2 keys", shown: true
  }]);
  assert.deepEqual(metrics($("ledgerMetrics")), {
    "Plugin": "bounded_evidence", "Health": "healthy", "Epoch": "epoch-b",
    "Keys": "2/32 filled", "Max age": "5000 ms", "Evidence publisher": "bounded_evidence"
  });
  assert.deepEqual(keys(), ["thing:a", "thing:b"]);
  assert.equal($("statusText").textContent, "Live | bounded_evidence: 2 keys");

  // Two plugins: both listed, the publisher shown by default.
  latest = publication([BOUNDED, RECORDING], "bounded_evidence");
  await ctx.refresh();
  assert.deepEqual(pluginRows().map((row) => [row.id, row.label, row.shown]), [
    ["bounded_evidence", "plugin · evidence publisher", true],
    ["recording_test", "plugin", false]
  ]);
  assert.equal(pluginRows()[1].meta, "no health · epoch epoch-1 · 1 keys");

  // Keyboard activation selects the other plugin; the publisher stays named.
  const recording = $("pluginList").querySelectorAll(".plugin-row")[1];
  recording.focus();
  for (const handler of $("pluginList").listeners.click || []) {
    handler({ target: recording, detail: 0, preventDefault() {} });
  }
  assert.deepEqual(pluginRows().map((row) => row.shown), [false, true]);
  assert.equal(metrics($("ledgerMetrics"))["Plugin"], "recording_test");
  assert.equal(metrics($("ledgerMetrics"))["Evidence publisher"], "bounded_evidence");
  assert.deepEqual(keys(), ["rec-1"]);
  assert.equal(ctx.document.activeElement.dataset.plugin, "recording_test");
  assert($("pluginList").contains(ctx.document.activeElement));

  // Pointer selection continues to work while live updates rebuild the list.
  const bounded = $("pluginList").querySelectorAll(".plugin-row")[0];
  $("pluginList").listeners.pointerdown[0]({ target: bounded, preventDefault() {} });
  assert.equal(metrics($("ledgerMetrics"))["Plugin"], "bounded_evidence");
  assert.deepEqual(keys(), ["thing:a", "thing:b"]);

  // No publisher and no listed pick: nothing is shown by position.
  latest = publication([
    { plugin_id: "alpha", state: { record_count: 0, records: [] } },
    { plugin_id: "beta", state: { record_count: 0, records: [] } }
  ], null);
  await ctx.refresh();
  assert.deepEqual(pluginRows().map((row) => row.shown), [false, false]);
  assert.equal(metrics($("ledgerMetrics"))["Plugin"], "pick a plugin");
  assert.equal(metrics($("ledgerMetrics"))["Evidence publisher"], "none");
  assert.equal($("keyList").textContent, "No evidence publisher. Pick a plugin above to see its keys.");
  assert.equal($("statusText").textContent, "Live | 2 plugins, no evidence publisher");
}

function perceptionPage() {
  const ctx = pageContext(() => new Promise(() => {}));
  runPage("cli/automa_cli/perception_view.html", ctx);
  const frameMetrics = () => metrics(ctx.document.getElementById("frameMetrics"));
  const render = (payload) => vm.runInContext(`latest = ${JSON.stringify(payload)}; renderData();`, ctx);

  render(publication([BOUNDED], "bounded_evidence"));
  assert.equal(frameMetrics()["Memory keys"], "bounded_evidence 2");
  assert.equal(frameMetrics()["Evidence publisher"], "bounded_evidence");

  render(publication([BOUNDED, RECORDING], "bounded_evidence"));
  assert.equal(frameMetrics()["Memory keys"], "bounded_evidence 2, recording_test 1");
  assert.equal(frameMetrics()["Evidence publisher"], "bounded_evidence");

  render({ ...publication([], null), memory: null });
  assert.equal(frameMetrics()["Memory keys"], "open /memory");
  assert.equal(frameMetrics()["Evidence publisher"], "—");
}

(async () => {
  await memoryPage();
  perceptionPage();
  console.log("runtime view memory ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
