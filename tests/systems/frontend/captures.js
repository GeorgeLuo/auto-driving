const assert = require("assert/strict");
const fs = require("fs");
const vm = require("vm");

const painted = [];
const nodes = {};
function makeNode() {
  return {
    hidden: false,
    dataset: {},
    style: { setProperty() {} },
    addEventListener() {},
    querySelector() { return makeNode(); },
    querySelectorAll() { return []; },
  };
}

async function page() {
  let view;
  const ctx = vm.createContext({
    URL, URLSearchParams,
    location: new URL("file:///v2/index.html?fast"),
    document: {
      getElementById(id) { return nodes[id] || (nodes[id] = makeNode()); },
      querySelectorAll() { return []; },
    },
    window: {},
    setTimeout: queueMicrotask,
    setInterval() {},
    render() {},
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
  await api.read("/api/captures");
  const press = (label) => view().keys.find((key) => key.label === label).action();
  return { view, press, paint: () => Captures.paint() };
}

(async () => {
  const { view, press, paint } = await page();
  assert.equal(view().rows.length, 2);
  assert.match(view().rows[0].name, /chase\s+run-closed/);
  assert.equal(view().rows[0].marked, false);
  assert.match(view().rows[1].name, /run-active/);
  assert.equal(view().rows[1].marked, true);

  await view().rows[0].action();
  assert.match(view().status, /Run run-closed/);
  assert.match(view().status, /Format entry file/);
  assert.match(view().status, /Frames 2/);
  assert.deepEqual(Array.from(view().keys, (key) => key.label), ["VIEW"]);

  await press("VIEW");
  paint();
  assert.match(view().mode, /^1\/2 · \d{4}-\d{2}-\d{2} .* · steer 0.1  throttle 0.2$/);
  const first = painted.at(-1);
  assert.equal(first.schema, "host_frame_publication");
  assert.equal(first.report.cycle.perception.things[0].thing_id, "box");
  assert.equal(first.report.application.steering, 0.1);
  assert.equal(first.image.frame_index, 0);

  await press("NEXT");
  paint();
  assert.match(view().mode, /^2\/2 · \d{4}-\d{2}-\d{2} .* · steer 0.2  throttle 0.2$/);
  assert.equal(painted.at(-1).image.frame_index, 1);
  assert.equal(view().keys.find((key) => key.label === "NEXT").disabled, true);
  assert.equal(view().keys.find((key) => key.label === "FIRST").disabled, false);

  await press("FIRST");
  paint();
  assert.match(view().mode, /^1\/2 /);
  press("LIST");
  assert.equal(view().mode, "");
  assert.match(view().status, /Run run-closed/);

  await view().rows[1].action();
  assert.match(view().status, /Run run-active/);
  assert.equal(view().keys.find((key) => key.label === "VIEW"), undefined);
})().catch((error) => { console.error(error); process.exitCode = 1; });
