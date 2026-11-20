const assert = require("assert/strict");
const fs = require("fs");
const vm = require("vm");

const url = "http://127.0.0.1:5051/";
const route = "/api/simulators/delete";

async function page(names) {
  const requests = [];
  let view;
  const ctx = vm.createContext({
    URL, URLSearchParams,
    location: new URL("file:///v2/index.html"),
    document: {
      getElementById() { return { addEventListener() {} }; },
      querySelectorAll() { return []; },
    },
    window: {},
    Vehicles: { state: {} },
    setTimeout: queueMicrotask,
    setInterval() {},
    render() {},
  });
  function load(file) {
    vm.runInContext(fs.readFileSync("cli/automa_cli/frontend/pages/v2/js/" + file, "utf8"), ctx);
  }
  load("desk.js");
  ctx.deskWindow = (_root, getView) => { view = getView; };
  load("mock.js");
  load("api.js");
  load("simulators.js");
  const { api, mock } = vm.runInContext("({ api, mock })", ctx);
  mock.store.simulators.push({ url, port: 5051, state: "running" });
  for (const name of names) mock.store.hosts.push({ name, vehicleUrl: url });
  const write = api.write;
  api.write = (path, body, options) => {
    requests.push({ path, body: { ...body } });
    return write(path, body, options);
  };
  ctx.refresh = () => api.read("/api/simulators");
  await ctx.refresh();
  view().rows[0].action();
  await new Promise(setImmediate);
  const press = (label) => view().keys.find((key) => key.label === label).action();
  return { view, requests, press, store: mock.store };
}

(async () => {
  const { view, requests, press, store } = await page(["alpha", "bravo"]);
  press("DEL");
  assert.equal(view().status, "Delete 127.0.0.1:5051 and hosts alpha, bravo?");
  assert.deepEqual(Array.from(view().keys, (key) => key.label), ["YES", "NO"]);
  press("NO");
  assert.match(view().status, /used by hosts alpha, bravo/);
  assert.deepEqual(requests, []);
  assert.equal(store.simulators.length, 1);
  assert.equal(store.hosts.length, 2);

  press("DEL");
  await press("YES");
  assert.deepEqual(requests, [{ path: route, body: { url, with_hosts: true } }]);
  assert.equal(view().rows.length, 0);
  assert.equal(store.hosts.length, 0);
  assert.match(view().status, /Stopped host alpha[^\n]*\nStopped host bravo[^\n]*\nDeleted Simulator/);

  const alone = await page([]);
  alone.press("DEL");
  assert.equal(alone.view().status, "Delete?");
  await alone.press("YES");
  assert.deepEqual(alone.requests, [{ path: route, body: { url } }]);
  assert.equal(alone.view().rows.length, 0);
  assert.match(alone.view().status, /^Deleted Simulator/);
})().catch((error) => { console.error(error); process.exitCode = 1; });
