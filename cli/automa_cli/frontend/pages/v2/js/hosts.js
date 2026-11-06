// Hosts: hosts status's list; hosts create, update, delete; hosts run, stop, restart. Run and
// restart take the run form, as both take hosts run's flags. A started run opens a Stream window
// on its host, in perception.
"use strict";

const Hosts = (() => {
  const W = { mode: "rows", selected: null, log: [], ask: null, busy: false, reading: false };
  const root = $("hostsFrame");
  const field = (form, name) => root.querySelector(`[data-form=${form}] [data-field=${name}]`);
  const listing = () => (api.docs["/api/hosts/status"] || { hosts: [] }).hosts;
  const find = (name) => listing().find((h) => h.host_name === name);
  // The host created at this vehicle (the first, when there are several).
  const at = (url) => listing().find((h) => h.vehicle_url === url);

  // ---- The host's block, as hosts status prints it (host_formatting.host_block). ----
  const LABELS = { "chase-sim": "chaser", picar: "PiCar" };
  const STATE_WORDS = { pre_start: "not started", finished_run: "finished run" };
  // A run's mode follows its state; manual, the mode when no run has control, adds nothing.
  function stateText(h) {
    const text = STATE_WORDS[h.state] || String(h.state || "unknown");
    return typeof h.mode === "string" && h.mode && h.mode !== "manual" ? `${text}, ${h.mode.replaceAll("_", " ")}` : text;
  }
  const count = (n, noun) => (Number.isInteger(n) ? `${n} ${noun}${n === 1 ? "" : "s"}` : null);
  const pad = (n) => String(n).padStart(2, "0");
  const localTime = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  function frameAge(ms) {
    if (!Number.isInteger(ms)) return null;
    const age = Math.max(0, (Date.now() - ms) / 1000);
    return age < 60 ? `last frame ${age.toFixed(1)}s ago` : `last frame at ${localTime(new Date(ms))}`;
  }
  function attachment(h) {
    if (h.state === "not found") return null;
    if (h.attached) {
      const since = new Date(h.attached_since);
      return h.attached_since ? `attached since ${isNaN(since) ? h.attached_since : localTime(since)}` : "attached";
    }
    if (h.simulator === "not answering") return "simulator not answering";
    const local = h.local === "host record" ? "local host record" : h.local;
    if (local === "staged connection" || h.attached == null) return local;  // a PiCar is not a simulator host
    return local ? `not attached to its simulator (${local})` : "not attached to its simulator";
  }
  function lines(h) {
    const activity = [count(h.decision_count, "decision"), count(h.error_count, "error"), frameAge(h.last_frame_at_ms)].filter(Boolean);
    const address = [h.url, h.pid == null ? null : `pid ${h.pid}`].filter(Boolean);
    return [`Host ${h.host_name}  ${stateText(h)}`,
      ...(h.vehicle_url ? [`  ${LABELS[h.provider]} at ${h.vehicle_url}`] : []),
      ...(activity.length ? [`  ${activity.join(", ")}`] : []),
      ...(h.error ? String(h.error).split("\n").map((line) => `  ${line}`) : []),
      ...(attachment(h) ? [`  ${attachment(h)}`] : []),
      ...(address.length ? [`  ${address.join(", ")}`] : [])];
  }

  const back = () => { Object.assign(W, { ask: null, mode: "rows", log: [] }); render(); };
  // Selecting a host reads hosts status again; selecting it twice clears the pane.
  function select(name) {
    if (W.selected === name) { W.selected = null; back(); return; }
    Object.assign(W, { mode: "rows", selected: name });
    readFor(W, "/api/hosts/status");
  }
  function openCreate(url) {
    Object.assign(W, { mode: "create", selected: null, ask: null, log: [] });
    field("create", "name").value = "";
    fillVehicles();
    if (url) field("create", "vehicle").value = url;
    render();
    field("create", "name").focus();
  }
  function fillVehicles() {
    const usable = Vehicles.usable();
    fillSelect(field("create", "vehicle"),
               usable.length ? usable.map((v) => [v.url, `${new URL(v.url).host} · ${v.provider}`]) : [["", "no vehicle can take a host"]]);
  }
  // A name already taken answers under the open form, as hosts create would.
  function create() {
    const name = field("create", "name").value.trim();
    const v = Vehicles.usable().find((x) => x.url === field("create", "vehicle").value);
    if (!v) return;
    if (find(name)) { W.log = [`Host ${name} already exists.`]; render(); return; }
    W.mode = "rows";
    command(W, "/api/hosts/create", { name, provider: v.provider, url: v.url });
  }
  // hosts run's flags: --observe-only, --num-decisions N or --unbounded, --record.
  function runBody(name) {
    const observe = field("run", "mode").value === "observe_only";
    const count = field("run", "count").value;
    return { name, observe_only: observe, num_decisions: count === "unbounded" ? null : Number(count),
             unbounded: count === "unbounded" && !observe, record: field("run", "record").value === "on" };
  }
  async function start(route, body) {
    W.mode = "rows";
    await command(W, route, body);
    if (find(body.name)?.state === "running") Stream.openPerception(body.name);
  }
  function remove(h) {
    W.selected = null;
    command(W, "/api/hosts/delete", { name: h.host_name });
  }
  // From Vehicles' HOST: the vehicle's host is selected here; otherwise a new host's form, filled in for it.
  function show(vehicle) {
    bringUp("hostsFrame");
    const h = at(vehicle.url);
    if (h) { W.selected = null; select(h.host_name); return; }
    openCreate(vehicle.url);
  }

  function view() {
    const h = find(W.selected);
    const ask = (what) => () => { Object.assign(W, { ask: what, log: [] }); render(); };
    const openRun = (mode) => () => { Object.assign(W, { mode, log: [] }); render(); };
    let keys = [];
    if (W.busy || W.reading) keys = [];
    else if (W.ask === "delete") keys = [{ label: "YES", action: () => remove(h) }, { label: "NO", action: back }];
    else if (W.mode === "create") keys = [{ label: "NEW", action: create, disabled: !Vehicles.usable().length || !field("create", "name").value.trim() },
                                          { label: "CANCEL", action: back }];
    else if (W.mode === "run" || W.mode === "restart") keys = [
      { label: W.mode === "run" ? "RUN" : "RSTRT", action: () => start(`/api/hosts/${W.mode}`, runBody(h.host_name)), gap: "runProgress" },
      { label: "CANCEL", action: back }];
    else if (h) keys = [
      ...(h.state === "running"
        ? [{ label: "STOP", action: () => command(W, "/api/hosts/stop", { name: h.host_name }) },
           { label: "RSTRT", action: openRun("restart") }]
        : [{ label: "RUN", action: openRun("run"), disabled: h.state === "not answering" }]),
      { label: "UPD", action: () => command(W, "/api/hosts/update", { name: h.host_name }) },
      { label: "DEL", action: ask("delete") },
    ];
    return {
      mode: { create: "new host", run: "run", restart: "restart" }[W.mode] || "",
      verbs: [{ label: "NEW", action: () => openCreate(null), disabled: W.busy || W.mode === "create" }],
      rows: listing().map((x) => ({ name: x.host_name, selected: W.selected === x.host_name, disabled: W.busy, action: () => select(x.host_name) })),
      form: { create: "create", run: "run", restart: "run" }[W.mode] || null,
      // An open form shows only the error it answers.
      status: W.ask === "delete" ? "Delete?" : W.log.length ? W.log.join("\n") : W.mode !== "rows" ? "" : W.reading ? "..." : h ? lines(h).join("\n") : "",
      statusGap: "noStream",
      keys,
    };
  }

  const w = deskWindow(root, view);
  return { state: W, listing, find, at, lines, show, paint: () => { fillVehicles(); paintWindow(w); } };
})();
