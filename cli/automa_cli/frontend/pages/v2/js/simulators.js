// Simulators: simulators status's list, what simulators create started; simulators create and
// delete. VEC goes to the simulator in Vehicles, or to discover filled in for it.
"use strict";

const Simulators = (() => {
  const W = { selected: null, log: [], ask: null, busy: false, reading: false };
  const listing = () => (api.docs["/api/simulators"] || { simulators: [] }).simulators;

  // The simulator's block, as simulators status --url prints it.
  function lines(s) {
    return [`Simulator (chase-sim)  ${s.state} at ${s.url}`,
      ...(s.frontend === "connected" && s.game ? [`  frontend connected, game ${s.game}`] : s.frontend ? [`  frontend ${s.frontend}`] : []),
      ...(s.error ? [`  ${s.error}`] : []),
      ...(s.pid ? [`  created by simulators create, pid ${s.pid}`] : []),
      ...(s.hosts.length ? [`  used by host${s.hosts.length > 1 ? "s" : ""} ${s.hosts.join(", ")}`] : []),
      ...(s.state === "exited" ? [`  Simulator log ${s.log}:`, ...s.log_tail.map((line) => `  ${line}`)] : [])];
  }

  // Selecting a simulator reads simulators status again; selecting it twice clears the pane.
  function select(url) {
    if (W.selected === url) { Object.assign(W, { selected: null, ask: null, log: [] }); render(); return; }
    W.selected = url;
    readFor(W, "/api/simulators");
  }
  function create() {
    W.selected = null;
    command(W, "/api/simulators/create", {});
  }
  // A refused delete (a host uses it) keeps the simulator selected.
  async function remove(s) {
    const result = await command(W, "/api/simulators/delete", { url: s.url });
    if (result.ok && W.selected === s.url) { W.selected = null; render(); }
  }

  function view() {
    const s = listing().find((x) => x.url === W.selected);
    const keys = W.ask ? [{ label: "YES", action: () => remove(s) }, { label: "NO", action: () => { W.ask = null; render(); } }]
      : s && !W.busy && !W.reading ? [
          // An exited simulator is no vehicle: there is nothing to show or discover.
          ...(s.state === "exited" ? [] : [{ label: "VEC", action: () => Vehicles.show(s), disabled: Vehicles.state.busy }]),
          { label: "DEL", action: () => { Object.assign(W, { ask: "delete", log: [] }); render(); } },
        ]
      : [];
    return {
      verbs: [{ label: "NEW", action: create, disabled: W.busy, gap: "simProgress" }],
      rows: listing().map((x) => ({ name: new URL(x.url).host, selected: W.selected === x.url, exited: x.state === "exited",
                                    disabled: W.busy, action: () => select(x.url) })),
      status: W.ask ? "Delete?" : W.log.length ? W.log.join("\n") : W.reading ? "..." : s ? lines(s).join("\n") : "",
      keys,
    };
  }

  const w = deskWindow($("simulatorsFrame"), view);
  return { state: W, listing, paint: () => paintWindow(w) };
})();
