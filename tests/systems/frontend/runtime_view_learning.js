// Runs the /learning page script against a small fake DOM, fed the
// /api/learning answers that test_runtime_view_learning.py builds with the
// real history, and checks the scoreboard, the focused belief's charts, the
// plugin explorer, and that the page follows the since cursor and restarts.
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const payloads = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));

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
    setAttribute(name, value) {
      this.attributes[name] = String(value);
      if (name === "class") this.className = String(value);
    },
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
    contains(node) { return node === this || this.descendants().includes(node); }
  };
}

function pageContext(fetch) {
  const byId = new Map();
  const ctx = {
    console,
    window: { addEventListener() {} },
    setInterval() {},
    fetch,
    document: {
      getElementById(id) {
        if (!byId.has(id)) byId.set(id, fakeElement());
        return byId.get(id);
      },
      createElement: fakeElement,
      createElementNS: () => fakeElement()
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

async function settle() {
  for (let index = 0; index < 5; index += 1) await new Promise((resolve) => setImmediate(resolve));
}

(async () => {
  // Each answer is keyed by the since cursor the page must send.
  let answers = { 0: payloads.first };
  const sent = [];
  const ctx = pageContext(async (url) => {
    const since = new URL(url, "http://view.invalid").searchParams.get("since");
    sent.push(since);
    assert(since in answers, `unexpected since=${since}`);
    return { ok: true, json: async () => answers[since] };
  });
  runPage("cli/automa_cli/learning_view.html", ctx);
  await settle();
  const $ = (id) => ctx.document.getElementById(id);
  const rows = () => $("board").querySelectorAll(".belief-row");
  const cells = (index) => rows()[index].children.map((cell) => cell.textContent);
  const body = () => $("beliefBody");
  const bars = () => body().querySelectorAll("bar").map((bar) => bar.className.replace("bar ", ""));
  const ticks = () => body().querySelectorAll("tick").map((tick) => tick.className.replace("tick ", ""));
  const readout = (title) => {
    const chart = body().querySelectorAll("chart").find((box) => box.children[0].children[0].textContent === title);
    assert(chart, `no chart titled ${title}`);
    return chart.children[0].children[1].textContent;
  };

  // The first answer: two beliefs, each with one open trial and no checks.
  assert.deepEqual(sent, ["0"]);
  assert.equal($("vehicle").textContent, "unknown vehicle");
  assert.deepEqual(rows().map((row) => row.dataset.belief), ["approach_model.run_ms", "stop_row.row"]);
  assert.equal(cells(0)[2], "no checks");
  assert.equal(rows()[0].getAttribute("aria-pressed"), "true");
  assert.equal($("beliefTitle").textContent, "approach_model.run_ms");
  assert.deepEqual(bars(), ["open"]);
  assert.equal($("statusText").textContent, "Live | 2 beliefs · 1 plugin");

  // The next poll asks only for later rows and merges them.
  answers = { [payloads.first.seq]: payloads.second };
  await ctx.refresh();
  assert.deepEqual(sent, ["0", String(payloads.first.seq)]);
  assert.equal(cells(0)[2], "0.500 · n 2");
  assert.equal(cells(0)[3], "run_duration 1✓ 1✗");
  assert.deepEqual(ticks(), ["support", "contradict"]);
  assert.deepEqual(bars(), ["support", "contradict"]);
  assert.equal(readout("run_duration: predicted vs observed"), "coverage 1/2 (50%)");
  assert.equal(readout("/value · /properties p25–p75"), "1300 [1100, 1500]");
  assert.equal(readout("confidence · 90% interval"), "0.500 · n 2");

  // Pointer focus moves to the withdrawn stop trial and its reason.
  $("board").listeners.pointerdown[0]({ target: rows()[1], preventDefault() {} });
  assert.equal($("beliefTitle").textContent, "stop_row.row");
  assert.equal(rows()[1].getAttribute("aria-pressed"), "true");
  assert.equal(cells(1)[6], "1 / 0");
  assert.match(rows()[1].children[6].title, /stop\/no_stop 1/);
  assert.deepEqual(bars(), ["withdrawn"]);
  assert.match(body().querySelectorAll("bar")[0].children[0].textContent, /withdrawn \(no_stop\)/);
  assert.equal(body().querySelectorAll("tick").length, 0);

  // Keyboard activation moves it back.
  for (const handler of $("board").listeners.click) handler({ target: rows()[0], detail: 0, preventDefault() {} });
  assert.equal($("beliefTitle").textContent, "approach_model.run_ms");

  // The plugin explorer draws the row family as a profile and the phase as a lane.
  assert.deepEqual($("pluginTabs").querySelectorAll("plugin-tab").map((tab) => tab.className), ["plugin-tab selected"]);
  assert($("pluginBody").querySelectorAll("chart").some((box) => box.children[0].children[0].textContent === "/state/rows/*"));
  assert.deepEqual($("pluginBody").querySelectorAll("lane-seg").map((segment) => segment.children[0].textContent),
    ["/state/phase: idle", "/state/phase: timing", "/state/phase: idle"]);

  // A restarted view has a new history: the page starts it again from 0.
  answers = { [payloads.second.seq]: payloads.restarted_since, 0: payloads.restarted };
  await ctx.refresh();
  assert.deepEqual(sent.slice(-2), [String(payloads.second.seq), "0"]);
  assert.deepEqual(rows().map((row) => row.dataset.belief), ["stop_row.row"]);
  assert.equal($("beliefTitle").textContent, "stop_row.row");
  assert.deepEqual(bars(), []);
  assert(body().textContent.includes("No trials in this window."));
  assert.equal(cells(0)[6], "1 / 0");

  // Paused, the page sends nothing.
  $("pauseButton").listeners.click[0]();
  assert.equal($("statusText").textContent, "Paused");
  await ctx.refresh();
  assert.equal(sent.length, 4);

  console.log("runtime view learning ok");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
