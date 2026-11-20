const assert = require("assert/strict");
const fs = require("fs");
const vm = require("vm");

const painted = [];
const nodes = {};
function makeNode() {
  const children = {};
  const listeners = {};
  return {
    hidden: false,
    disabled: false,
    min: "0",
    max: "0",
    value: "0",
    dataset: {},
    style: { setProperty() {} },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    dispatch(type) { for (const fn of listeners[type] || []) fn(); },
    querySelector(sel) { return children[sel] || (children[sel] = makeNode()); },
    querySelectorAll() { return []; },
  };
}

async function page() {
  let view;
  const timers = [];
  const ctx = vm.createContext({
    URL, URLSearchParams,
    location: new URL("file:///v2/index.html?fast"),
    document: {
      getElementById(id) { return nodes[id] || (nodes[id] = makeNode()); },
      querySelectorAll() { return []; },
    },
    window: {},
    setTimeout(fn, delay) {
      if (timers.manual && delay > 0) {
        const entry = { fn, delay, cleared: false };
        timers.push(entry);
        return entry;
      }
      queueMicrotask(fn);
      return null;
    },
    clearTimeout(entry) {
      if (entry) entry.cleared = true;
    },
    setInterval() {},
    render() { if (ctx.Captures) ctx.Captures.paint(); },
    Feed: {
      create() { return { pane: { hidden: true } }; },
      paint(_feed, doc) { painted.push(doc); },
    },
  });
  function load(file) {
    vm.runInContext(fs.readFileSync("cli/automa_cli/frontend/pages/v2/js/" + file, "utf8"), ctx);
  }
  load("desk.js");
  ctx.deskWindow = (_root, getView) => { view = getView; return { view: getView }; };
  ctx.paintWindow = (w) => w.view();
  load("mock.js");
  load("api.js");
  load("captures.js");
  const { api, Captures } = vm.runInContext("({ api, Captures })", ctx);
  ctx.Captures = Captures;
  await api.read("/api/captures");
  const press = (label) => view().keys.find((key) => key.label === label).action();
  const root = nodes.capturesFrame;
  const seek = root.querySelector("[data-seek]");
  const close = root.querySelector("[data-frame-close]");
  return { api, Captures, view, press, paint: () => Captures.paint(), root, seek, close, timers };
}

const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(setImmediate); };

(async () => {
  const { api, Captures, view, press, paint, seek, close, timers } = await page();
  paint();
  assert.equal(view().rows.length, 2);
  assert.match(view().rows[0].name, /chase\s+run-closed/);
  assert.equal(view().rows[0].marked, false);
  assert.match(view().rows[1].name, /run-active/);
  assert.equal(view().rows[1].marked, true);
  assert.equal(seek.hidden, true);
  assert.deepEqual(Array.from(view().keys, (key) => key.label), []);

  await view().rows[0].action();
  assert.match(view().status, /Run run-closed/);
  assert.match(view().status, /Format entry file/);
  assert.match(view().status, /Frames 4/);
  assert.equal(seek.hidden, true);
  assert.deepEqual(Array.from(view().keys, (key) => key.label), ["VIEW"]);

  await press("VIEW");
  paint();
  assert.equal(seek.hidden, false);
  assert.equal(seek.min, "0");
  assert.equal(seek.max, "3");
  assert.equal(seek.value, "0");
  assert.deepEqual(Array.from(view().keys, (key) => key.label), ["▶", "FIRST", "PREV", "NEXT", "LAST", "LIST"]);
  assert.match(view().mode, /^1\/4 · \d{4}-\d{2}-\d{2} .* · steer 0.1  throttle 0.2$/);
  const first = painted.at(-1);
  assert.equal(first.schema, "host_frame_publication");
  assert.equal(first.report.cycle.perception.things[0].thing_id, "box");
  assert.equal(first.report.application.steering, 0.1);
  assert.equal(first.image.frame_index, 0);

  // Key stepping updates the frame and moves the seeker.
  await press("NEXT");
  paint();
  assert.match(view().mode, /^2\/4 · \d{4}-\d{2}-\d{2} .* · steer 0.2  throttle 0.2$/);
  assert.equal(painted.at(-1).image.frame_index, 1);
  assert.equal(seek.value, "1");
  assert.equal(view().keys.find((key) => key.label === "FIRST").disabled, false);

  // Seeker sets the index and loads that frame.
  seek.value = "3";
  seek.dispatch("input");
  await settle();
  assert.equal(Captures.state.index, 3);
  assert.equal(seek.value, "3");
  assert.equal(painted.at(-1).image.frame_index, 3);
  assert.match(view().mode, /^4\/4 · \d{4}-\d{2}-\d{2} .* · steer 0.4  throttle 0.2$/);
  assert.equal(view().keys.find((key) => key.label === "NEXT").disabled, true);
  assert.equal(view().keys.find((key) => key.label === "LAST").disabled, true);

  await press("FIRST");
  paint();
  assert.match(view().mode, /^1\/4 /);
  assert.equal(seek.value, "0");

  // Only one frame read is in flight, and the latest requested position wins.
  const realRead = api.read;
  const inflight = [];
  const requested = [];
  api.read = (route, opts) => {
    if (!route.startsWith("/api/captures/frame?")) return realRead(route, opts);
    const index = Number(new URL(route, "http://localhost").searchParams.get("index"));
    requested.push(index);
    return new Promise((resolve) => {
      inflight.push({ index, resolve: async () => resolve(await realRead(route, opts)) });
    });
  };

  seek.value = "1";
  seek.dispatch("input");
  assert.equal(Captures.state.index, 1);
  assert.equal(view().mode, "2/4");
  assert.equal(inflight.length, 1);
  assert.deepEqual(requested, [1]);

  seek.value = "2";
  seek.dispatch("input");
  assert.equal(Captures.state.index, 2);
  assert.equal(view().mode, "3/4");
  assert.equal(inflight.length, 1);

  seek.value = "3";
  seek.dispatch("input");
  assert.equal(Captures.state.index, 3);
  assert.equal(view().mode, "4/4");
  assert.equal(inflight.length, 1);
  assert.deepEqual(requested, [1]);

  await inflight.shift().resolve();
  await settle();
  assert.equal(inflight.length, 1);
  assert.deepEqual(requested, [1, 3]);

  await inflight.shift().resolve();
  await settle();
  assert.equal(inflight.length, 0);
  assert.equal(painted.at(-1).image.frame_index, 3);
  assert.match(view().mode, /^4\/4 · \d{4}-\d{2}-\d{2} .* · steer 0.4  throttle 0.2$/);
  api.read = realRead;

  // Play advances frame by frame (never faster than frames load) and stops at the end.
  await press("FIRST");
  assert.equal(Captures.state.index, 0);
  assert.equal(view().keys[0].label, "▶");
  api.read = (route, opts) => {
    if (!route.startsWith("/api/captures/frame?")) return realRead(route, opts);
    const index = Number(new URL(route, "http://localhost").searchParams.get("index"));
    requested.push(index);
    return new Promise((resolve) => {
      inflight.push({ index, resolve: async () => resolve(await realRead(route, opts)) });
    });
  };
  requested.length = 0;
  press("▶");
  await settle();
  assert.equal(Captures.state.playing, true);
  assert.equal(Captures.state.index, 1);
  assert.equal(inflight.length, 1);
  assert.deepEqual(requested, [1]);
  await inflight.shift().resolve();
  await settle();
  assert.equal(Captures.state.index, 2);
  assert.equal(inflight.length, 1);
  assert.deepEqual(requested, [1, 2]);
  await inflight.shift().resolve();
  await settle();
  assert.equal(Captures.state.index, 3);
  assert.equal(inflight.length, 1);
  assert.deepEqual(requested, [1, 2, 3]);
  await inflight.shift().resolve();
  await settle();
  api.read = realRead;
  assert.equal(Captures.state.index, 3);
  assert.equal(Captures.state.playing, false);
  assert.equal(view().keys[0].label, "▶");
  assert.equal(seek.value, "3");
  assert.equal(painted.at(-1).image.frame_index, 3);

  // At the end (index 3), pressing play restarts from frame 0 and plays to the end.
  timers.manual = true;
  press("▶");
  assert.equal(Captures.state.index, 0);
  assert.equal(Captures.state.playing, true);
  assert.equal(view().keys[0].label, "❚❚");
  await settle();
  assert.equal(painted.at(-1).image.frame_index, 0);
  const stepTimer = timers.find((t) => !t.cleared);
  assert.ok(stepTimer);
  assert.ok(stepTimer.delay > 0 && stepTimer.delay <= 500);

  // Pause stops playback.
  press("❚❚");
  assert.equal(Captures.state.playing, false);
  assert.equal(view().keys[0].label, "▶");
  assert.equal(stepTimer.cleared, true);

  // Seeking stops playback.
  timers.length = 0;
  press("▶");
  assert.equal(Captures.state.playing, true);
  assert.equal(view().keys[0].label, "❚❚");
  seek.value = "2";
  seek.dispatch("input");
  assert.equal(Captures.state.playing, false);
  assert.equal(view().keys[0].label, "▶");
  await settle();
  assert.equal(Captures.state.index, 2);

  // Key presses stop playback.
  timers.length = 0;
  press("▶");
  assert.equal(Captures.state.playing, true);
  await press("PREV");
  assert.equal(Captures.state.playing, false);
  assert.equal(view().keys[0].label, "▶");
  assert.equal(Captures.state.index, 1);

  // Closing the window stops playback.
  timers.length = 0;
  press("▶");
  assert.equal(Captures.state.playing, true);
  close.dispatch("click");
  assert.equal(Captures.state.playing, false);
  assert.equal(view().keys[0].label, "▶");

  // LIST stops playback and hides viewer controls.
  timers.length = 0;
  press("▶");
  assert.equal(Captures.state.playing, true);
  press("LIST");
  assert.equal(Captures.state.playing, false);
  assert.equal(view().mode, "");
  assert.equal(seek.hidden, true);
  assert.deepEqual(Array.from(view().keys, (key) => key.label), ["VIEW"]);
  assert.match(view().status, /Run run-closed/);

  timers.manual = false;
  await view().rows[1].action();
  assert.match(view().status, /Run run-active/);
  assert.equal(seek.hidden, true);
  assert.equal(view().keys.find((key) => key.label === "VIEW"), undefined);
})().catch((error) => { console.error(error); process.exitCode = 1; });
