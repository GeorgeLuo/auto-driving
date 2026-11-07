// Plugins: plugins list for one host, or the packaged catalog. The list is the steps, the
// selected step's plugins under it; HOSTS chooses the host. The pane manages what is selected:
// the host (plugins save, disarm --all), a step (a preset, plugins stream, disarm --step) or a
// plugin (arm, disarm). UPLD is plugins upload.
"use strict";

const Plugins = (() => {
  const STEPS = ["perception", "observation", "memory", "proposal", "plan", "action"];
  const ONE_PLUGIN = new Set(["observation", "plan", "action"]);  // arming another replaces the one
  const CLEARABLE = new Set(["perception", "memory", "proposal"]);  // disarm --step and --all clear these
  const W = { mode: "rows", ask: null, log: [], busy: false, host: null, context: "steps", selected: null, streaming: null };
  const root = $("pluginsFrame");
  const field = (name) => root.querySelector(`[data-field=${name}]`);

  // ---- plugins list's documents: the packaged catalog, and each host's. ----
  const packaged = () => api.docs[routes.plugins(null)];
  const listing = (host) => (host ? api.docs[routes.plugins(host)] : packaged());
  const defaults = (step) => packaged()?.defaults[step] || [];
  // Each step's presets, as plugins list --json gives them: their plugins, the default marked.
  const presets = (step) => packaged()?.step_presets?.[step] || {};
  const defaultPreset = (step) => Object.keys(presets(step)).find((name) => presets(step)[name].default) || null;
  // What runs in a step: what the host armed, or the defaults in the packaged catalog.
  const armed = (host, step) => (host ? (listing(host)?.armed || []).filter((p) => p.step === step).map((p) => p.id) : defaults(step));
  const definitions = (host, step) => (listing(host)?.catalog.plugins || []).filter((p) => p.step === step);
  const ids = (host, step) => definitions(host, step).map((p) => p.id);
  const uploaded = (host, step, id) => definitions(host, step).find((p) => p.id === id && p.metadata.revision)?.metadata;
  const sourceOf = (host, step, id) => { const u = uploaded(host, step, id); return u ? `revision ${u.revision}` : "packaged"; };

  // plugins list, or plugins list --long for a host: each step's armed plugins, then the rest.
  function catalogLines(host) {
    if (!host) return ["Catalog: packaged", ...STEPS.flatMap((step) => ids(null, step)
      .map((id) => `- ${step}/${id}: packaged${defaults(step).includes(id) ? " (default)" : ""}`))];
    const catalog = listing(host)?.catalog || {};
    return [`Host: ${host}`, `Catalog version: ${catalog.version ?? 1} (host run ${catalog.host_run_id || "none"})`,
      ...STEPS.flatMap((step) => {
        const on = armed(host, step);
        return [...on, ...ids(host, step).filter((id) => !on.includes(id))].map((id) => {
          const u = uploaded(host, step, id);
          return `- ${step}/${id}: ${u ? `revision ${u.revision} (${u.filename})` : "packaged"}${on.includes(id) ? " (armed)" : ""}`;
        });
      })];
  }
  // plugins list --step: * marks what is armed; the step's default preset is named as its default.
  function stepLines(step, host) {
    const preset = defaultPreset(step), on = armed(host, step);
    return [`${titleCase(step)}${ONE_PLUGIN.has(step) ? ", one plugin" : ""}${preset ? `, default preset ${preset}` : ""}`,
            `Catalog: ${host ? `host ${host}` : "packaged"}`,
            ...ids(null, step).map((id) => `${on.includes(id) ? "*" : " "} ${id} (packaged)${defaults(step).includes(id) ? " default" : ""}`)];
  }
  const pluginLines = (host, step, id) => [`${step}/${id} (${sourceOf(host, step, id)})`,
    `  ${armed(host, step).includes(id) ? `armed on ${host || "nothing (no host)"}` : "not armed"}`,
    `  ${defaults(step).includes(id) ? `a default of ${step}` : "not a default"}`];

  // ---- Its commands. ----
  const target = () => ({ host_name: W.host });
  const back = () => { Object.assign(W, { mode: "rows", ask: null, log: [] }); render(); };
  // A selection belongs to its step; a stream of another step ends.
  function select(next) {
    W.selected = next;
    if (W.streaming && W.streaming !== (next || "").split("/")[0]) W.streaming = null;
    back();
  }
  function change(step, id, wanted) {
    command(W, wanted ? "/api/plugins/arm" : "/api/plugins/disarm", { ...target(), name: [`${step}/${id}`] });
  }
  function openPreset(step) {
    Object.assign(W, { mode: "preset", log: [], streaming: null });
    const names = Object.keys(presets(step));
    fillSelect(field("preset"), names.map((name) => [name, name]));
    field("preset").value = defaultPreset(step) || names[0];
    render();
  }
  function armPreset(step) {
    W.mode = "rows";
    command(W, "/api/plugins/arm", { ...target(), preset: `${step}/${field("preset").value}` });
  }
  function openUpload() {
    Object.assign(W, { mode: "upload", ask: null, log: [], streaming: null });
    field("step").value = (W.selected || "perception").split("/")[0];
    for (const name of ["name", "entrypoint", "config"]) field(name).value = "";
    field("file").value = "";
    field("arm").value = "off";
    render();
    field("name").focus();
  }
  const base64 = (file) => new Promise((done, fail) => {
    const reader = new FileReader();
    reader.onload = () => done(String(reader.result).split(",")[1] || "");
    reader.onerror = () => fail(reader.error);
    reader.readAsDataURL(file);
  });
  // --config must be a JSON object; the form stays open on that error.
  async function upload() {
    let config;
    try { config = JSON.parse(field("config").value.trim() || "{}"); } catch { config = null; }
    if (!config || typeof config !== "object" || Array.isArray(config)) { W.log = ["--config must be a JSON object."]; render(); return; }
    const file = field("file").files[0];
    const body = { ...target(), name: `${field("step").value}/${field("name").value.trim()}`, entrypoint: field("entrypoint").value.trim(),
                   config, arm: field("arm").value === "on", filename: file.name, source_base64: await base64(file) };
    W.mode = "rows";
    command(W, "/api/plugins/upload", body);
  }
  const disarmAll = (step) => command(W, "/api/plugins/disarm", step ? { ...target(), step } : { ...target(), all: true });
  root.querySelector("[data-file-pick]").addEventListener("click", () => field("file").click());

  // From Stream's PLGS: this window on the stream's host, its step selected.
  function show(host, step) {
    Object.assign(W, { host, selected: step, context: "steps", mode: "rows", ask: null, log: [], streaming: null });
    bringUp("pluginsFrame");
    render();
  }

  function view() {
    const host = W.host;
    const verbs = [
      { label: "HOSTS", action: () => { Object.assign(W, { context: W.context === "hosts" ? "steps" : "hosts", log: [] }); render(); } },
      { label: "UPLD", action: openUpload, disabled: !host || W.busy || W.mode === "upload" || W.context === "hosts" },
    ];
    // HOSTS: the hosts, and packaged for none; choosing one goes back to its steps.
    if (W.context === "hosts") {
      const choose = (name) => () => {
        if (W.host !== name) Object.assign(W, { host: name, streaming: null, selected: null });
        Object.assign(W, { context: "steps", mode: "rows", ask: null, log: [] });
        render();
      };
      const h = Hosts.find(host);
      return {
        mode: "choose host", verbs,
        rows: [{ name: "packaged", selected: !host, action: choose(null) },
               ...Hosts.listing().map((x) => ({ name: x.host_name, selected: host === x.host_name, action: choose(x.host_name) }))],
        status: h ? Stream.feedLines(h).join("\n") : "Catalog: packaged",
      };
    }
    const [step, id] = (W.selected || "").split("/");
    const clearable = step && CLEARABLE.has(step) ? step : null;
    const streaming = !!W.streaming;
    const streamKeys = step && step !== "observation" ? [
      { label: streaming ? "STOP" : "STRM", action: () => { Object.assign(W, { streaming: streaming ? null : step, log: [] }); render(); }, disabled: !host },
      { label: "VIEW", action: () => Stream.open({ host, step }), disabled: !host, gap: step === "proposal" || step === "plan" ? "stepViews" : null },
    ] : [];
    const askDisarm = () => { Object.assign(W, { ask: "disarm", log: [], streaming: null }); render(); };
    const uploadReady = field("name").value.trim() && field("file").files[0] && field("entrypoint").value.trim();
    let keys = [];
    if (W.busy) keys = [];
    else if (W.ask) keys = [{ label: "YES", action: () => disarmAll(clearable) }, { label: "NO", action: back }];
    else if (W.mode === "preset") keys = [{ label: "ARM", action: () => armPreset(step) }, { label: "CANCEL", action: back }];
    else if (W.mode === "upload") keys = [{ label: "UPLD", action: upload, disabled: !uploadReady }, { label: "CANCEL", action: back }];
    else if (id) {
      // A one-plugin step is changed by arming another, never by disarming its one.
      const on = armed(host, step).includes(id);
      keys = [...(on ? (ONE_PLUGIN.has(step) ? [] : [{ label: "DSARM", action: () => change(step, id, false), disabled: !host }])
                     : [{ label: "ARM", action: () => change(step, id, true), disabled: !host }]), ...streamKeys];
    } else if (step) keys = [
      ...(Object.keys(presets(step)).length ? [{ label: "PRST", action: () => openPreset(step), disabled: !host }] : []),
      ...streamKeys,
      ...(clearable ? [{ label: "DSALL", action: askDisarm, disabled: !host }] : []),
    ];
    else keys = [{ label: "SAVE", action: () => command(W, "/api/plugins/save", target()), disabled: !host },
                 { label: "DSALL", action: askDisarm, disabled: !host }];
    const rows = STEPS.flatMap((name) => [
      { name, selected: W.selected === name, disabled: W.busy,
        action: () => select(W.selected === name || W.selected?.startsWith(`${name}/`) ? null : name) },
      ...(step === name ? ids(host, name).map((plugin) => ({
        name: plugin, child: true, marked: armed(host, name).includes(plugin), selected: W.selected === `${name}/${plugin}`, disabled: W.busy,
        action: () => select(W.selected === `${name}/${plugin}` ? name : `${name}/${plugin}`),
      })) : []),
    ]);
    const status = W.ask ? (clearable ? `Disarm every ${clearable} plugin?` : "Disarm every plugin?")
      : W.log.length ? W.log.join("\n")
      : W.mode === "preset" ? `plugins: ${(presets(step)[field("preset").value]?.plugins || []).join(", ")}`
      : W.mode === "upload" ? ""
      : streaming ? api.docs[routes.stream(host, step)]?.text || `Reading ${step} on ${host}...`
      : id ? pluginLines(host, step, id).join("\n")
      : step ? stepLines(step, host).join("\n")
      : catalogLines(host).join("\n");
    const doing = W.ask ? "disarm" : W.mode === "preset" ? `${step} preset` : W.mode === "upload" ? "upload" : streaming ? `${step} stream` : "catalog";
    return {
      mode: `${host || "packaged"} · ${doing}`, verbs, rows,
      form: W.mode === "preset" || W.mode === "upload" ? W.mode : null,
      status, keys, keepScroll: streaming,
    };
  }

  const w = deskWindow(root, view);
  function paint() {
    if (W.host && !Hosts.find(W.host)) W.host = null;  // a deleted host leaves the packaged catalog
    const picked = field("file").files[0];
    setText(root.querySelector("[data-file-name]"), picked ? picked.name : "");
    paintWindow(w);
  }
  // The read this window streams, for the page's polling.
  const streamed = () => (W.streaming && W.host ? [routes.stream(W.host, W.streaming)] : []);
  return { state: W, armed, defaultPreset, show, streamed, paint };
})();
