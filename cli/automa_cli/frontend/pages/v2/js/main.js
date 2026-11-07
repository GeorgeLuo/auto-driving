// The page: every window's paint, the column's readouts, and the reads that keep them current.
// Nothing tells the page what changed (gap noNotify), so it reads every list again after each
// command and polls: what each stream shows (a host's latest frame, or a step's probe) every tick,
// hosts status every HOSTS_EVERY ticks.
"use strict";

const TICK_MS = typeof mock !== "undefined" ? mock.ms(250) || 25 : 250;
const HOSTS_EVERY = 8;
const WINDOWS = [Vehicles, Hosts, Plugins, Simulators];

function paintColumn() {
  const hosts = Hosts.listing().length, created = Simulators.listing().length, streams = Stream.count();
  setText($("vehiclesReadout"), `${Vehicles.usable().length}/${Vehicles.listing().length} up`);
  setText($("hostsReadout"), `${hosts} host${hosts === 1 ? "" : "s"}`);
  setText($("pluginsReadout"), Plugins.state.host || "packaged");
  setText($("streamReadout"), streams ? `${streams} open` : "");
  setText($("simulatorsReadout"), created ? `${created} created` : "none");
}
function render() {
  for (const win of WINDOWS) win.paint();
  Stream.paint();
  paintColumn();
  if (typeof mock !== "undefined") mock.paint();
}

// Every list the page shows, and each host's plugins.
async function refresh() {
  const [, , hosts] = await Promise.all(["/api/vehicles", "/api/simulators", "/api/hosts/status", routes.plugins(null)].map(api.read));
  await Promise.all((hosts.hosts || []).map((h) => api.read(routes.plugins(h.host_name))));
  render();
}
let ticks = 0;
async function poll() {
  ticks += 1;
  const streamed = new Set([...Stream.streamed(), ...Plugins.streamed()]);
  await Promise.all([...[...streamed].map(api.read),
                     ...(ticks % HOSTS_EVERY === 0 ? [api.read("/api/hosts/status")] : [])]);
  render();
  setTimeout(poll, TICK_MS);
}

// Every load: all windows closed, a third of the page tall and twice as wide as tall. Two stacks
// beside the column: left, in its order, Simulators, Vehicles, Hosts; right, Plugins, with
// Stream windows opening below it. Seeded before float.js binds the windows.
(function seedLayout() {
  const column = document.querySelector("main").getBoundingClientRect();
  const gap = 16, h = Math.round(innerHeight / 3), w = 2 * h;
  const left = Math.max(8, column.left - gap - w);
  const right = Math.min(Math.max(8, innerWidth - w - 8), column.right + gap);
  const layout = {
    simulatorsFrame: [left, 0], vehiclesFrame: [left, h], hostsFrame: [left, 2 * h], pluginsFrame: [right, 0],
    gapsFrame: [Math.max(8, (innerWidth - w) / 2), innerHeight - h - 44],  // above the mock controls
  };
  for (const [id, [x, y]] of Object.entries(layout)) {
    localStorage.setItem(`automa-float-${id}`, JSON.stringify({ position: { x, y }, size: { width: w, height: h }, minimized: false, closed: true }));
  }
})();

$("streamOpen").addEventListener("click", () => Stream.open());
document.addEventListener("DOMContentLoaded", () => {
  render();
  refresh();
  poll();
});
