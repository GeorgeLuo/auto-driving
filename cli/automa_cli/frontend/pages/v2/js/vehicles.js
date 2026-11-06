// Vehicles: vehicles status's list; vehicles discover and remove. HOST goes to the vehicle's
// host in Hosts, or to a new host's form filled in for it.
"use strict";

const Vehicles = (() => {
  const W = { mode: "rows", selected: null, log: [], ask: null, busy: false, reading: false };
  const root = $("vehiclesFrame");
  const field = (name) => root.querySelector(`[data-form=discover] [data-field=${name}]`);
  const listing = () => (api.docs["/api/vehicles"] || { vehicles: [] }).vehicles;
  // A vehicle can take a host unless it does not answer.
  const usable = () => listing().filter((v) => v.state !== "unreachable");
  const HEADINGS = { picar: "PiCar (picar)", "chase-sim": "Simulator (chase-sim)" };

  // The vehicle's block, as vehicles status prints it.
  function lines(v) {
    const head = `${HEADINGS[v.provider]}  ${v.state} at ${v.url}`;
    if (!("frontend" in v)) return [head + (v.error ? `: ${v.error}` : "")];
    return [head,
      ...(v.frontend === "connected" && v.game ? [`  frontend connected, game ${v.game}`] : v.frontend ? [`  frontend ${v.frontend}`] : []),
      ...(v.error ? [`  ${v.error}`] : []),
      ...(v.pid ? [`  created by simulators create, pid ${v.pid}`] : [])];
  }

  const clear = () => Object.assign(W, { ask: null, selected: null, mode: "rows", log: [] });
  // Selecting a vehicle reads vehicles status again; selecting it twice clears the pane.
  function select(url) {
    if (W.selected === url) { clear(); render(); return; }
    Object.assign(W, { mode: "rows", selected: url });
    readFor(W, "/api/vehicles");
  }
  function openDiscover(provider, url) {
    Object.assign(W, { mode: "discover", selected: null, ask: null, log: [] });
    field("provider").value = provider;
    field("url").value = url;
    render();
  }
  function discover() {
    const body = { provider: field("provider").value, url: field("url").value.trim() };
    Object.assign(W, { mode: "rows", selected: null });
    command(W, "/api/vehicles/discover", body);
  }
  function untrack(v) {
    W.selected = null;
    command(W, "/api/vehicles/remove", { url: v.url });
  }
  // From Simulators' VEC: a listed simulator is selected here; another opens discover, filled in for it.
  function show(simulator) {
    bringUp("vehiclesFrame");
    if (listing().some((v) => v.url === simulator.url)) { W.selected = null; select(simulator.url); return; }
    openDiscover("chase-sim", simulator.url);
  }

  function view() {
    const v = listing().find((x) => x.url === W.selected);
    const keys = W.ask ? [{ label: "YES", action: () => untrack(v) }, { label: "NO", action: () => { W.ask = null; render(); } }]
      : W.mode === "discover" ? [{ label: "DISC", action: discover, disabled: !field("url").value.trim() },
                                 { label: "CANCEL", action: () => { clear(); render(); } }]
      : v && !W.reading && !W.busy ? [
          { label: "HOST", action: () => Hosts.show(v), disabled: Hosts.state.busy || (!Hosts.at(v.url) && v.state === "unreachable") },
          { label: "UNTRK", action: () => { Object.assign(W, { ask: "remove", log: [] }); render(); } },
        ]
      : [];
    return {
      mode: W.mode === "discover" ? "discover" : "",
      verbs: [{ label: "DISC", action: () => { openDiscover("picar", ""); field("url").focus(); }, disabled: W.busy || W.mode === "discover" }],
      rows: listing().map((x) => ({ name: new URL(x.url).host, selected: W.selected === x.url, disabled: W.busy, action: () => select(x.url) })),
      form: W.mode === "discover" ? "discover" : null,
      status: W.ask ? "Untrack?" : W.log.length ? W.log.join("\n") : W.reading ? "..." : v ? lines(v).join("\n") : "",
      statusGap: "vehicleUrl",
      keys,
    };
  }

  const w = deskWindow(root, view);
  return { state: W, listing, usable, show, paint: () => paintWindow(w) };
})();
