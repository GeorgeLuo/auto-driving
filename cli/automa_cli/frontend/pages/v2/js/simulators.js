// Simulators: simulators status's list, what simulators create started; simulators create, with
// its form for --backend and --config, and delete. VEC goes to the simulator in Vehicles, or to
// discover filled in for it.
"use strict";

const Simulators = (() => {
  const W = { mode: "rows", selected: null, log: [], ask: null, busy: false, reading: false };
  const root = $("simulatorsFrame");
  const field = (name) => root.querySelector(`[data-form=create] [data-field=${name}]`);
  const listing = () => (api.docs["/api/simulators"] || { simulators: [] }).simulators;

  // The simulator's block, as simulators status --url --long prints it, without the log line an
  // exited simulator's tail already names.
  function lines(s) {
    const source = s.backend === "native" ? `Native executable ${s.executable}` : `Metrics UI checkout ${s.metrics_ui_dir}`;
    return [`Simulator (chase-sim)  ${s.state} at ${s.url}`,
      ...(s.frontend === "connected" && s.game ? [`  frontend connected, game ${s.game}`] : s.frontend ? [`  frontend ${s.frontend}`] : []),
      ...(s.error ? [`  ${s.error}`] : []),
      ...(s.pid ? [`  created by simulators create, pid ${s.pid}`] : []),
      ...(s.hosts.length ? [`  used by host${s.hosts.length > 1 ? "s" : ""} ${s.hosts.join(", ")}`] : []),
      `  ${source}, config ${s.config}`,
      ...(s.state === "exited" ? [`  Simulator log ${s.log}:`, ...s.log_tail.map((line) => `  ${line}`)] : [])];
  }

  // Selecting a simulator reads simulators status again; selecting it twice clears the pane.
  function select(url) {
    if (W.selected === url) { Object.assign(W, { mode: "rows", selected: null, ask: null, log: [] }); render(); return; }
    Object.assign(W, { mode: "rows", selected: url });
    readFor(W, "/api/simulators");
  }
  const back = () => { Object.assign(W, { mode: "rows", ask: null, log: [] }); render(); };
  // The form keeps its last backend and config, so starting another of the same takes one key.
  function openCreate() {
    Object.assign(W, { mode: "create", selected: null, ask: null, log: [] });
    render();
    field("config").focus();
  }
  function create() {
    W.mode = "rows";
    command(W, "/api/simulators/create", { backend: field("backend").value, config: field("config").value.trim() || null });
  }
  // A refused delete (a host uses it) keeps the simulator selected.
  async function remove(s) {
    const result = await command(W, "/api/simulators/delete", { url: s.url });
    if (result.ok && W.selected === s.url) { W.selected = null; render(); }
  }

  function view() {
    const s = listing().find((x) => x.url === W.selected);
    const keys = W.busy || W.reading ? []
      : W.ask ? [{ label: "YES", action: () => remove(s) }, { label: "NO", action: () => { W.ask = null; render(); } }]
      : W.mode === "create" ? [{ label: "NEW", action: create }, { label: "CANCEL", action: back }]
      : s ? [
          // An exited simulator is no vehicle: there is nothing to show or discover.
          ...(s.state === "exited" ? [] : [{ label: "VEC", action: () => Vehicles.show(s), disabled: Vehicles.state.busy }]),
          { label: "DEL", action: () => { Object.assign(W, { ask: "delete", log: [] }); render(); } },
        ]
      : [];
    return {
      mode: W.mode === "create" ? "new simulator" : "",
      verbs: [{ label: "NEW", action: openCreate, disabled: W.busy || W.mode === "create" }],
      rows: listing().map((x) => ({ name: new URL(x.url).host, selected: W.selected === x.url, exited: x.state === "exited",
                                    disabled: W.busy, action: () => select(x.url) })),
      form: W.mode === "create" ? "create" : null,
      // An open form shows only the error it answers.
      status: W.ask ? "Delete?" : W.log.length ? W.log.join("\n") : W.mode !== "rows" ? "" : W.reading ? "..." : s ? lines(s).join("\n") : "",
      keys,
    };
  }

  const w = deskWindow(root, view);
  return { state: W, listing, paint: () => paintWindow(w) };
})();
