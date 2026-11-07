// Mock only. The viewer's routes, answered from an in-page store on timers, so the page runs
// with nothing behind it. A read answers the document its command's --json prints; a write does
// what its command does to the store and answers what it prints, line by line. ROUTES marks the
// routes the viewer has (viewer_commands.py); the rest are the ones the page needs next, and
// GAPS says what the CLI lacks for them.
// Opened from disk, the mock answers every route. Served by the viewer (at /v2/), it answers
// only the routes the viewer lacks; every route the page reads and writes is the viewer's now.
// A new route: add it here, its answers shaped as the command's, then mark it viewer once the
// viewer has it. plugins upload --manifest cannot read a path from the page, so any .json path
// uploads MANIFEST, a stand-in for an external collection.
"use strict";

const mock = (() => {
  const FAST = new URLSearchParams(location.search).has("fast");
  const SERVED = location.protocol !== "file:";
  const ms = (n) => (FAST ? Math.round(n / 25) : n);
  const after = (wait) => new Promise((done) => setTimeout(done, ms(wait)));
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const hex = (n) => Array.from({ length: n }, () => "0123456789abcdef"[Math.floor(Math.random() * 16)]).join("");
  const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
  const CAPTURE = window.CAPTURE || [];  // a recorded chase-sim run stands in for each host's publications

  const GAPS = {
    simProgress: "simulators create prints nothing until its simulator is ready (up to 45 s).",
    noNotify: "No change notification: every list read probes each host and vehicle under a 5 s deadline, so the page reads everything again after each command and polls hosts.",
    runProgress: "hosts run starts the run in the background and answers after its startup wait; startup progress (RunEvent) reaches only a foreground monitor.",
    noStream: "The viewer's writes answer once, when the command ends: hosts create, update and delete print their progress as they go, and the page shows it all at the end.",
    noCancel: "Nothing cancels simulators create or a host deploy once started.",
    recordings: "No command lists recorded runs, so the workbench cannot offer a host's recordings as sources.",
    stepViews: "Proposal and plan have no runtime view of their own; the Action view carries them as candidates and selection.",
    vehicleUrl: "vehicles status has no --url: showing one vehicle reads the whole list, probing every listed vehicle under one deadline.",
    fixedVehicle: "A host's vehicle is fixed at hosts create: hosts update has no --url, so moving a host is delete + create.",
  };
  const GAP_KEYS = Object.keys(GAPS);

  // The collection an uploaded manifest names, its plugins in manifest order with their source files.
  const MANIFEST = { name: "chase-413", plugins: [
    "perception/sim_cardboard_obstacle", "perception/sim_cardboard_permanence", "perception/sim_wall", "perception/view_stagnation",
    "perception/clearance", "perception/closeness", "memory/stop_row", "memory/turn_model", "memory/approach_model",
    "proposal/drive_forward", "proposal/drive_backwards", "proposal/avoid_obstacle", "plan/select_proposal",
  ].map((key) => { const [step, id] = key.split("/"); return { step, id, filename: `chase/${step}/plugins/${id}/plugin.py` }; }) };

  // implementations/decision_cycle/<step>/catalog.json, as plugins list prints them.
  const STEPS = {
    perception: { defaults: ["frame", "floor_plane"],
                  ids: ["frame", "floor_plane", "classical_regions", "composite_box_fusion", "composite_box_fusion_object_separated",
                        "floor_continuity", "floor_continuity_capture", "floor_continuity_temporal", "motion_tracks",
                        "multi_obstruction_tracks", "sim_cardboard_obstacle", "sim_cardboard_permanence", "sim_color_targets"],
                  presets: { lightweight_observer: ["frame", "floor_plane"], sim_debug: ["frame", "sim_color_targets"],
                             "chase-v1-detect-by-color": ["sim_cardboard_obstacle", "sim_cardboard_permanence"],
                             visual_observer: ["frame", "floor_plane", "motion_tracks"],
                             multi_obstruction: ["multi_obstruction_tracks", "floor_continuity", "classical_regions"],
                             obstruction_observer: ["frame", "floor_plane", "multi_obstruction_tracks"] } },
    observation: { one: true, defaults: ["perception_summary"], ids: ["perception_summary"] },
    memory: { defaults: ["bounded_evidence"], ids: ["bounded_evidence"], presets: { recency_ledger: ["bounded_evidence"] } },
    proposal: { defaults: ["avoid_recent_obstruction"], ids: ["avoid_recent_obstruction"], presets: { obstruction_avoidance: ["avoid_recent_obstruction"] } },
    plan: { one: true, defaults: ["highest_confidence"], ids: ["highest_confidence"], presets: { most_confident: ["highest_confidence"] } },
    action: { one: true, defaults: ["selected"], ids: ["selected", "hold", "mode"] },
  };
  const STEP_NAMES = Object.keys(STEPS);
  const CLEARABLE = ["perception", "memory", "proposal"];  // disarm --all leaves the one-plugin steps
  const defaultSelection = () => Object.fromEntries(STEP_NAMES.map((step) => [step, [...STEPS[step].defaults]]));
  // The step's defaults are its preset of the same plugins.
  const defaultPreset = (step) => Object.keys(STEPS[step].presets || {}).find((name) => same(STEPS[step].presets[name], STEPS[step].defaults));
  function selectionLabel(step, ids) {
    if (!ids.length) return "(none)";
    const preset = defaultPreset(step);
    return preset && same(ids, STEPS[step].defaults) ? `${preset} (${ids.join(", ")})` : ids.join(", ");
  }

  // ---- The store: what simulators create started, what vehicles discover listed, the hosts. ----
  const store = { simulators: [], vehicles: [], hosts: [] };
  const findHost = (name) => store.hosts.find((h) => h.name === name);
  // The endpoints vehicles discover can reach; anything else refuses the connection.
  const REACHABLE = {
    "http://piracer.local:8887": { provider: "picar", state: "reachable, no host listening" },
    "http://127.0.0.1:5060/": { provider: "chase-sim", state: "running" },
  };
  const HEADINGS = { picar: "PiCar (picar)", "chase-sim": "Simulator (chase-sim)" };
  // What discover prints for a URL nothing in REACHABLE answers: a PiCar that does not answer in
  // time (one that refuses the connection is reachable and listed), a simulator that is not there.
  // The simulator's names its control URL, as the CLI prints it.
  const UNREADY = {
    picar: (url) => `No PiCar can take a host at ${url}: GET ${url}/host/status did not answer within 5 s`,
    "chase-sim": (url) => `No Chase simulator can take a host at ws://${new URL(url).host}/ws/control: Passive capture `
      + "could not complete state_before after 3ms: [Errno 61] Connection refused Recovery: No simulator answers here. "
      + "./cli/automa simulators create starts a headless one.",
  };
  const heading = (provider, url) => `${HEADINGS[provider]} at ${url}`;
  function normalizedUrl(provider, url) {
    const u = new URL(url);
    if (!/^https?:$/.test(u.protocol)) throw new TypeError("not http(s)");
    return provider === "chase-sim" ? `${u.protocol}//${u.host}/` : `${u.protocol}//${u.host}`;
  }
  const refused = (error) => ({ ok: false, error });
  const missingHost = (name) => ({ ok: false, kind: "missing", text: `No host named ${name}.` });

  // ---- Reads ----
  // vehicles status: each created simulator still listed and running, then each discovered vehicle.
  function vehicles() {
    const chase = (state) => ({ frontend: state === "running" ? "connected" : null, game: state === "running" ? "chase" : null });
    return { schema: "automa_vehicles_status", vehicles: [
      ...store.simulators.filter((s) => s.listed && s.state === "running")
        .map((s) => ({ provider: "chase-sim", url: s.url, state: s.state, error: null, ...chase(s.state), pid: s.pid })),
      ...store.vehicles.map((v) => ({ provider: v.provider, url: v.url, state: v.state, error: null,
                                      ...(v.provider === "chase-sim" ? { ...chase(v.state), pid: null } : {}) })),
    ] };
  }
  // simulators status: every created simulator, an exited one with its log's tail.
  function simulators() {
    return { schema: "automa_simulators_status", simulators: store.simulators.map((s) => ({
      url: s.url, port: s.port, pid: s.pid, state: s.state,
      frontend: s.state === "running" ? "connected" : null, game: s.state === "running" ? "chase" : null, error: null,
      hosts: store.hosts.filter((h) => h.vehicleUrl === s.url).map((h) => h.name),
      started_at_ms: s.startedAt, metrics_ui_dir: "~/Projects/metrics-ui", config: "default",
      log: `runtime/simulators/${s.port}/simulator.log`, log_tail: s.state === "exited" ? s.logTail : null,
    })) };
  }
  // hosts status --json.
  function hosts() {
    return { schema: "automa_hosts_status", host_name: null, hosts: store.hosts.map((h) => ({
      host_name: h.name, url: h.url, pid: h.pid, attached: true, attached_since: h.since, local: "host record", simulator: null,
      provider: h.provider, vehicle_url: h.vehicleUrl, state: h.state, mode: h.mode, decision_count: h.decisions,
      error_count: 0, last_frame_at_ms: h.lastFrameAt, error: null,
    })) };
  }
  // A catalog's definitions: the packaged plugins, then what was uploaded to the host.
  function definitions(h) {
    const metadata = (step, id) => { const u = h && h.uploads[`${step}/${id}`]; return u ? { revision: u.revision, filename: u.filename } : {}; };
    const packaged = STEP_NAMES.flatMap((step) => STEPS[step].ids.map((id) => ({ step, id, metadata: metadata(step, id) })));
    const uploaded = h ? Object.keys(h.uploads).map((key) => key.split("/")).filter(([step, id]) => !STEPS[step].ids.includes(id))
      .map(([step, id]) => ({ step, id, metadata: metadata(step, id) })) : [];
    return [...packaged, ...uploaded];
  }
  const noHost = (name) => refused(`No host '${name}'. ${store.hosts.length ? `Hosts: ${store.hosts.map((h) => h.name).join(", ")}.` : "No hosts exist."}`);
  // plugins list --json: the packaged catalog with each step's defaults and presets, or with a
  // host (--long) its whole catalog and what it armed.
  function plugins({ host_name: name }) {
    if (!name) return { schema: "automa_plugin_list", ok: true, host_name: null, url: null, step: null,
      defaults: Object.fromEntries(STEP_NAMES.map((step) => [step, [...STEPS[step].defaults]])),
      step_presets: Object.fromEntries(STEP_NAMES.map((step) => [step, Object.fromEntries(Object.entries(STEPS[step].presets || {})
        .map(([preset, ids]) => [preset, { plugins: [...ids], default: preset === defaultPreset(step) }]))])),
      catalog: { source: "implementations/decision_cycle/*/catalog.json", plugins: definitions(null) } };
    const h = findHost(name);
    if (!h) return { ...noHost(name), schema: "automa_plugin_list", host_name: name, armed: null, arming_error: null };
    return { schema: "automa_plugin_list", ok: true, host_name: h.name, url: h.url, step: null, arming_error: null,
      armed: STEP_NAMES.flatMap((step) => h.armed[step].map((id) => ({ step, id, metadata: {} }))),
      catalog: { source: h.url, version: h.catalogVersion, host_run_id: h.runId, plugins: definitions(h), collections: copy(h.collections) } };
  }
  // A running host's frame: the capture's frame at its decision count, keeping only what the
  // perception plugins it has armed found.
  function captured(h) {
    if (!(h.state === "running" || h.decisions > 0) || !CAPTURE.length) return null;
    const frame = CAPTURE[h.decisions % CAPTURE.length];
    const armed = h.armed.perception;
    const recorded = frame.perception.plugin_runs || [];
    const runs = armed.map((id) => recorded.find((run) => run.plugin_id === id)
      || { plugin_id: id, status: "ok", duration_ms: 1 + (id.length % 4), signal_count: 0, thing_count: 0, error: null });
    const things = (frame.perception.things || []).filter((t) => armed.includes(t.source_plugin_id));
    // A host whose run ended keeps its last frame, older than its 1000 ms ceiling.
    const age = h.state === "running" ? 4 : Date.now() - h.lastFrameAt;
    const stale = age > 1000 ? `The latest frame is ${age} ms old, past its 1000 ms ceiling.` : null;
    return { frame, index: h.decisions, age, stale, perception: { ...frame.perception, plugin_runs: runs, things } };
  }
  // A host's latest frame, as the perception page reads it; the image is the capture's.
  function latest({ host_name: name }) {
    const h = findHost(name);
    if (!h) return noHost(name);
    const c = captured(h);
    if (!c) return refused("The frame publication health is 'unavailable'.");
    const { frame, index, age, stale, perception } = c, id = frame.frame.frame_id;
    return { schema: "host_frame_publication", host_name: h.name, health: stale ? "stale" : "healthy", ok: true, read_at_ms: Date.now(),
      result_age_ms: age, stale_after_ms: 1000, host_telemetry: null,
      image: { frame_id: id, frame_index: index, captured_at_ms: frame.frame.captured_at_ms, content_type: "image/jpeg",
               width_px: frame.frame.width_px, height_px: frame.frame.height_px, url: frame.image },
      report: { schema: "host_frame_report", host_name: h.name, frame_id: id, frame_index: index, run_id: h.runId,
        generation_id: frame.stream.generation_id, status: "ok", error: null, cycle: { perception, memory: frame.memory },
        values: { timing: { duration_ms: frame.cycle.cycle_duration_ms, steps: [{ step: "perception", duration_ms: frame.cycle.perception_ms }] } } } };
  }
  // The preset each step's armed plugins are, as the perception stream names them.
  const presetsOf = (h) => Object.fromEntries(STEP_NAMES.filter((step) => STEPS[step].presets)
    .map((step) => [step, Object.keys(STEPS[step].presets).find((name) => same(STEPS[step].presets[name], h.armed[step])) || "custom"]));
  // plugins stream --once --step: the probe, and as text the screen it prints.
  function stream({ host_name: name, step }) {
    const schema = ["perception", "memory"].includes(step) ? `host_${step}_live` : `host_${step}_stream`;
    const h = findHost(name);
    if (!h) return { schema, status: "unavailable", host_name: name, text: noHost(name).error };
    const c = captured(h);
    if (!c) return { schema, status: "waiting", host_name: h.name, text: `Host ${h.name} has no fresh ${step} record yet.` };
    const { frame, index, age, stale, perception } = c, st = frame.stream, id = frame.frame.frame_id, endpoint = `${h.url}/host/observation/latest`;
    const status = stale ? "stale" : "live", mode = h.mode || "manual";
    if (step === "perception") {
      const armed = h.armed.perception;
      const text = st.perception_text.split("\n").filter((line) => {
        const m = line.match(/^(?:plugin_run id|signal id=\S+ plugin|thing id=\S+ plugin)=(\S+)/);
        return !m || armed.includes(m[1]);
      }).map((line) => (line.startsWith("plugins=") ? `plugins=${armed.join(",")}` : line)).join("\n");
      const presets = presetsOf(h);
      return { schema, status, host_name: h.name, endpoint, frame_id: id, frame_index: index, captured_at_ms: frame.frame.captured_at_ms,
        age_ms: age, signal_count: st.signal_count, thing_count: perception.things.length, mode, presets, perception_duration_ms: frame.cycle.perception_ms,
        ...(stale ? { error: stale } : {}),
        text: ["automa perception stream", "", `host: ${h.name}`, `url: ${h.url}`, `status: ${status}`, ...(stale ? [`error: ${stale}`] : []),
          `presets: ${Object.entries(presets).sort().map(([k, v]) => `${k}=${v}`).join(" ")}  mode: ${mode}`,
          `control: steering=${st.control.steering}  throttle=${st.control.throttle}  reason=${st.control.reason}`,
          `cadence: cycle_ms=${st.cycle_ms}  age_ms=${age}`,
          `latest: frame=${id}  captured_at_ms=${frame.frame.captured_at_ms}  signals=${st.signal_count}  things=${perception.things.length}`,
          "publication: healthy  json: /host/observation/latest", "", "latest perception", "-----------------", text].join("\n") };
    }
    if (step === "memory") {
      const m = st.memory, line = (x) => `  ${x.plugin_id}: health=${x.health} epoch=${x.epoch_id} records=${x.record_count} max_records=32 max_age_ms=10000 eviction=oldest_first`;
      if (stale) return { schema, status, host_name: h.name, endpoint, frame_id: id, generation_id: st.generation_id, mode, error: stale,
        text: [`Live memory: ${h.name} [stale]`, `Endpoint: ${endpoint}`, `Mode: ${mode}`, `Detail: ${stale}`, "", `publication memory: ${stale}`].join("\n") };
      return { schema, status, host_name: h.name, endpoint, frame_id: id, generation_id: st.generation_id, mode,
        plugin_ids: h.armed.memory, evidence_publisher: m.publisher, last_duration_ms: 0.6, health: "healthy",
        text: [`Live memory: ${h.name} [live]`, `Endpoint: ${endpoint}`, `Mode: ${h.mode || "-"}`,
          `Applied plugins: ${h.armed.memory.join(", ") || "(none)"}`, ...m.plugins.map(line), `Evidence publisher: ${m.publisher || "none"}`,
          "Last duration: 0.600 ms", "", `publication memory: evidence publisher ${m.publisher || "none"}`, ...m.plugins.map(line)].join("\n") };
    }
    return { schema, status, host_name: h.name, frame_id: id, frame_index: index, run_id: h.runId, generation_id: st.generation_id,
      plugins: h.armed[step], freshness: { age_ms: age, max_age_ms: 1000 }, timing: { duration_ms: st.cycle_ms }, ...(stale ? { error: stale } : {}),
      text: [...(stale ? [stale] : []), `${titleCase(step)} stream: ${h.name} frame=${id} run=${h.runId} age_ms=${age} max_age_ms=1000`, `Generation: ${st.generation_id}`,
        `Plugins: ${h.armed[step].join(", ") || "none"}`, ...st[step], ...(step === "action" ? [st.application] : []),
        `Timing: ${step} 0.040 ms; cycle ${st.cycle_ms} ms`].join("\n") };
  }

  // ---- Writes ----
  // A command's output on a timer: strings are printed, numbers wait, functions are its effects.
  function play(kind, lines, onLine, pace = 260) {
    return new Promise((done) => {
      const out = [];
      const next = (i) => {
        if (i >= lines.length) { done({ ok: true, kind, text: out.join("\n") }); return; }
        const line = lines[i];
        if (typeof line === "function") line();
        else if (typeof line === "string") { out.push(line); onLine(line); }
        setTimeout(() => next(i + 1), ms(typeof line === "number" ? line : pace));
      };
      next(0);
    });
  }

  // vehicles discover: probes the URL (up to 5 s) and lists what answers.
  async function discover({ provider, url }) {
    if (!(provider in HEADINGS)) return refused("provider must be one of chase-sim, picar");
    let normal;
    try { normal = normalizedUrl(provider, String(url || "").trim()); }
    catch { return { ok: false, kind: "error", text: "The URL must be an http(s) URL." }; }
    if (vehicles().vehicles.some((v) => v.url === normal)) return { ok: false, kind: "listed", text: `Already listed: ${heading(provider, normal)}` };
    await after(900);
    const created = store.simulators.find((s) => s.url === normal && s.state === "running");
    const found = REACHABLE[normal];
    if (created && provider === "chase-sim") created.listed = true;
    else if (found && found.provider === provider) store.vehicles.push({ provider, url: normal, state: found.state });
    else return { ok: false, kind: "unready", text: UNREADY[provider](normal) };
    return { ok: true, kind: "discovered", text: `Discovered ${heading(provider, normal)}` };
  }
  // vehicles remove: forgets a discovered vehicle; a created simulator keeps running, unlisted.
  async function untrack({ url }) {
    const discovered = store.vehicles.find((v) => v.url === url);
    const created = store.simulators.find((s) => s.url === url && s.listed && s.state === "running");
    if (!discovered && !created) {
      const names = vehicles().vehicles.map((v) => heading(v.provider, v.url)).join(", ");
      return { ok: false, kind: "missing", text: `No listed vehicle at ${url}. ${names ? `Listed: ${names}.` : "None listed."}` };
    }
    if (discovered) store.vehicles.splice(store.vehicles.indexOf(discovered), 1);
    if (created) created.listed = false;
    await after(150);
    return { ok: true, kind: "removed", text: `Removed ${heading(discovered ? discovered.provider : "chase-sim", url)}` };
  }
  // simulators create: prints nothing until Metrics UI's headless launcher answers (up to 45 s).
  async function createSimulator() {
    const used = new Set([...store.simulators, ...store.vehicles].map((v) => Number(new URL(v.url).port)));
    let port = 5051;
    while (used.has(port)) port += 1;
    const url = `http://127.0.0.1:${port}/`;
    await after(1800);
    store.simulators.push({ url, port, pid: 48000 + Math.floor(Math.random() * 999), state: "running", listed: true,
                            startedAt: Date.now(), logTail: null });
    return { ok: true, kind: "created", text: `Created ${heading("chase-sim", url)}` };
  }
  // simulators delete: refused while a host was created at it; a running one is stopped first.
  async function deleteSimulator({ url }) {
    const s = store.simulators.find((x) => x.url === url);
    if (!s) {
      const urls = store.simulators.map((x) => x.url).join(", ");
      return { ok: false, kind: "missing", text: `No created simulator at ${url}. ${urls ? `Created: ${urls}.` : "None created."}` };
    }
    const users = store.hosts.filter((h) => h.vehicleUrl === url).map((h) => h.name);
    if (users.length) {
      const subject = users.length === 1 ? `Host ${users[0]} uses` : `Hosts ${users.join(", ")} use`;
      return { ok: false, kind: "blocked", text: `${subject} the simulator at ${url}; delete ${users.length === 1 ? "it" : "them"} first.` };
    }
    await after(s.state === "exited" ? 150 : 400);
    store.simulators.splice(store.simulators.indexOf(s), 1);
    return { ok: true, kind: "deleted", text: `Deleted ${heading("chase-sim", url)}${s.state === "exited" ? ", which had exited" : ""}` };
  }

  function createHost({ name, provider, url }, onLine) {
    if (!name) return { ok: false, kind: "error", text: "Name the host." };
    if (findHost(name)) return { ok: false, kind: "error", text: `Host ${name} already exists.` };
    const tree = hex(64), archiveHash = hex(64);
    const release = `autonomy-${tree.slice(0, 12)}-${Date.now()}.tar.gz`;
    const hostUrl = `http://127.0.0.1:${59000 + Math.floor(Math.random() * 999)}`;
    const pid = 51000 + Math.floor(Math.random() * 999);
    const armed = defaultSelection();
    return play("created", [
      "==> Package local controller bundle",
      `Archive: runtime/hosts/${name}/bundle/releases/${release}`,
      `Tree SHA-256: ${tree}`,
      `Archive SHA-256: ${archiveHash}`,
      "==> Extract controller bundle release",
      `Extracted release -> runtime/hosts/${name}/bundle`,
      () => store.hosts.push({ name, provider, vehicleUrl: url, url: hostUrl, pid, since: new Date().toISOString(), state: "pre_start", mode: null, lastFrameAt: null,
                               decisions: 0, count: null, runPid: null, runId: null, request: 0, armed, uploads: {}, collections: {},
                               catalogVersion: 1 }),
      `Host ${name} created for the chaser at ${url}`,
      `Release: ${release}`,
      `Archive SHA-256: ${archiveHash}`,
      ...STEP_NAMES.map((step) => `${titleCase(step)}: ${selectionLabel(step, armed[step])}`),
      "Decision generation: decision-da86a44eb2557b69",
      `Host ${name}  not started`,
      `  chaser at ${url}`,
      "  0 decisions, 0 errors",
      "  attached",
    ], onLine);
  }
  function startLines(h, flags) {
    const record = flags.includes("--record");
    const observe = flags.includes("--observe-only");
    const countIndex = flags.indexOf("--num-decisions");
    const count = countIndex >= 0 ? Number(flags[countIndex + 1]) : null;
    return [
      1500,  // hosts run prints nothing until a camera frame, its perception and a healthy view arrive, up to 20 s
      () => Object.assign(h, { state: "running", mode: observe ? "observe_only" : "autonomy", count, decisions: 0,
                               runPid: 52000 + Math.floor(Math.random() * 999), runId: `chase_sim-run-${hex(24)}` }),
      `Run started on host ${h.name}.`,
      ...STEP_NAMES.map((step) => `${titleCase(step)}: ${selectionLabel(step, h.armed[step])}`),
      record ? `Recording: runtime/hosts/${h.name}/bundle/runtime/automation/runs/chase_sim-run-${hex(24)}`
             : "Recording: off; latest frame and perception are overwritten each iteration",
      "Control source: runtime host",
      `Action policy: ${observe ? "observe_only" : "autonomy"}`,
      "Decision generation: decision-da86a44eb2557b69",
      "Runtime view: http://127.0.0.1:8996/",
      count === null ? "Decisions: unbounded" : `Decisions: ${count}`,
    ];
  }
  const stopLines = (h) => [() => Object.assign(h, { state: "stopped", mode: null }),
                            `Stopped the run on host ${h.name}.`, `PID: ${h.runPid}`,
                            `State: runtime/hosts/${h.name}/bundle/runtime/automation/state.json`];
  // hosts run's flags, from the body's fields of the same names.
  const runFlags = (body) => [...(body.observe_only ? ["--observe-only"] : []),
                              ...(body.num_decisions ? ["--num-decisions", String(body.num_decisions)] : []),
                              ...(body.unbounded ? ["--unbounded"] : []), ...(body.record ? ["--record"] : [])];
  function runHost(body, onLine) {
    const h = findHost(body.name);
    return h ? play("started", startLines(h, runFlags(body)), onLine) : missingHost(body.name);
  }
  function stopHost({ name }, onLine) {
    const h = findHost(name);
    return h ? play("stopped", stopLines(h), onLine) : missingHost(name);
  }
  // hosts restart takes run's flags again; it does not keep the last run's.
  function restartHost(body, onLine) {
    const h = findHost(body.name);
    return h ? play("started", [...stopLines(h), ...startLines(h, runFlags(body)).slice(1)], onLine) : missingHost(body.name);
  }
  function updateHost({ name }, onLine) {
    const h = findHost(name);
    if (!h) return missingHost(name);
    const tree = hex(64);
    return play("updated", ["==> Package local controller bundle",
      `Archive: runtime/hosts/${h.name}/bundle/releases/autonomy-${tree.slice(0, 12)}-${Date.now()}.tar.gz`,
      `Tree SHA-256: ${tree}`, `Archive SHA-256: ${hex(64)}`, `Host updated: ${h.name}`], onLine);
  }
  function deleteHost({ name }, onLine) {
    const h = findHost(name);
    if (!h) return missingHost(name);
    return play("deleted", [`${h.state === "running" ? "Stopped the run. " : ""}Stopped host ${h.name}. Removed runtime/hosts/${h.name}.`,
                            () => store.hosts.splice(store.hosts.indexOf(h), 1)], onLine);
  }

  const sourceOf = (h, step, id) => { const u = h.uploads[`${step}/${id}`]; return u ? `revision ${u.revision}` : "packaged"; };
  const listed = (h, step, ids) => ids.map((id) => `${id} (${sourceOf(h, step, id)})`).join(", ") || "(none)";
  // A selection change: the request, every step's requested plugins, then what the host applied.
  function selection(h, label, heading, next, onLine) {
    h.request += 1;
    const requested = { ...h.armed, ...next }, steps = [...STEP_NAMES].sort();
    return play("selection", [`Host: ${h.name}`, `Endpoint: ${h.url}`, ...heading, `${label}: requested`, `Request: ${h.request}`,
      ...steps.map((step) => `Requested ${step}: ${listed(h, step, requested[step])}`),
      () => Object.assign(h.armed, next), ...steps.map((step) => `Applied ${step}: ${listed(h, step, requested[step])}`)], onLine);
  }
  // A plugin armed into its step; a one-plugin step's replaces it.
  const withPlugin = (h, step, id) => (STEPS[step].one ? [id] : [...h.armed[step].filter((x) => x !== id), id]);
  const stepItem = (value) => { const [step, id] = String(value || "").split("/"); return STEPS[step] && id ? [step, id] : null; };
  // A collection's members by step, each step's in manifest order; refused when the host has none by that name.
  const members = (h, collection) => h.collections[collection];
  const unknownCollection = (h, label, collection) => ({ ok: false, kind: "error", text: [`Host: ${h.name}`, `Endpoint: ${h.url}`,
    `Collection: ${collection}`, `${label}: failed`, `Reason: unknown plugin collection '${collection}'`,
    `Next: ./cli/automa plugins list --host-name ${h.name} --long`].join("\n") });
  // plugins arm --name step/id ..., --preset step/preset (the preset's plugins in place of the step's),
  // or --collection: its plugins added to each step, a one-plugin step's replaced.
  function arm({ host_name: name, name: names, preset, collection }, onLine) {
    const h = findHost(name);
    if (!h) return noHost(name);
    if (collection !== undefined) {
      const by = members(h, collection);
      if (!by) return unknownCollection(h, "Arming", collection);
      const next = {};
      for (const [step, ids] of Object.entries(by)) for (const id of ids) next[step] = withPlugin({ armed: { ...h.armed, ...next } }, step, id);
      return selection(h, "Arming", [`Collection: ${collection}`], next, onLine);
    }
    if (preset !== undefined) {
      const [step, id] = stepItem(preset) || [];
      const ids = step && (STEPS[step].presets || {})[id];
      if (!ids) return refused(`unknown ${step} preset '${id}'; known: ${Object.keys(STEPS[step]?.presets || {}).sort().join(", ")}`);
      return selection(h, "Arming", [`Step: ${step}`, `Preset: ${id}`], { [step]: [...ids] }, onLine);
    }
    const items = (names || []).map(stepItem);
    if (!items.length || items.includes(null)) return refused("name must be a non-empty list of <step>/<plugin>");
    const next = {};
    for (const [step, id] of items) next[step] = withPlugin({ armed: { ...h.armed, ...next } }, step, id);
    return selection(h, "Arming", [], next, onLine);
  }
  // plugins disarm --name step/id ..., --collection (a one-plugin step keeps its plugin), --step, or --all.
  function disarm({ host_name: name, name: names, collection, step, all }, onLine) {
    const h = findHost(name);
    if (!h) return noHost(name);
    if (collection !== undefined) {
      const by = members(h, collection);
      if (!by) return unknownCollection(h, "Disarming", collection);
      const steps = Object.keys(by).filter((s) => CLEARABLE.includes(s));
      if (!steps.length) return refused(`plugin collection '${collection}' only arms one-plugin steps, which keep their plugin; arm a replacement instead`);
      return selection(h, "Disarming", [`Collection: ${collection}`],
                       Object.fromEntries(steps.map((s) => [s, h.armed[s].filter((id) => !by[s].includes(id))])), onLine);
    }
    if (names !== undefined) {
      const next = {};
      for (const [s, id] of names.map(stepItem).filter(Boolean)) next[s] = (next[s] || h.armed[s]).filter((x) => x !== id);
      return selection(h, "Disarming", [], next, onLine);
    }
    if (step === undefined && !all) return refused("disarm needs name, collection, step or all");
    const steps = all ? CLEARABLE : [step];
    return selection(h, "Disarming", step ? [`Step: ${step}`] : [], Object.fromEntries(steps.map((s) => [s, []])), onLine);
  }
  // plugins upload --name step/id --file --entrypoint [--config] [--arm].
  function upload({ host_name: name, name: key, filename, manifest, arm: andArm }, onLine) {
    const h = findHost(name);
    if (!h) return missingHost(name);
    if (manifest !== undefined) return uploadCollection(h, manifest, andArm, onLine);
    const [step, id] = key.split("/");
    const revision = ((h.uploads[key] || {}).revision || 0) + 1;
    const next = withPlugin(h, step, id);
    const armedText = next.map((x) => `${x} (${x === id ? `revision ${revision}` : sourceOf(h, step, x)})`).join(", ");
    return play("uploaded", [`Host: ${h.name}`, `Plugin: ${key}`, `Revision: ${revision}`,
      () => { h.uploads[key] = { revision, filename }; h.catalogVersion += 1; },
      `Catalog version: ${h.catalogVersion + 1} (host run none)`,
      ...(andArm ? [`Request: ${hex(12)}`, `Requested ${step}: ${armedText}`, () => { h.armed[step] = next; }, `Applied ${step}: ${armedText}`] : [])], onLine);
  }
  // plugins upload --manifest [--arm]: every plugin MANIFEST names as one revision, then the collection.
  function uploadCollection(h, manifest, andArm, onLine) {
    const head = [`Host: ${h.name}`, `Endpoint: ${h.url}`];
    if (!/\.json$/.test(manifest)) return { ok: false, kind: "error", text: [...head, "Upload: failed",
      `Reason: [Errno 2] No such file or directory: '${manifest}'`, `Next: Fix ${manifest.split("/").pop()}, then upload it again.`].join("\n") };
    const revision = hex(64), { name: collection, plugins } = MANIFEST;
    const by = {};
    for (const { step, id } of [...plugins].sort((a, b) => a.step.localeCompare(b.step))) (by[step] ||= []).push(id);
    const lines = [...head, "Upload: uploaded", `Collection: ${collection}`, `Revision: ${revision}`,
      () => { for (const { step, id, filename } of plugins) h.uploads[`${step}/${id}`] = { revision, filename };
              h.collections[collection] = by; h.catalogVersion += 1; },
      `Catalog version: ${h.catalogVersion + 1} (host run ${h.runId || "none"})`,
      ...plugins.map(({ step, id, filename }) => `- ${step}/${id}: revision ${revision} (${filename})`)];
    if (!andArm) return play("uploaded", [...lines, "Selection: unchanged",
      `Next: ./cli/automa plugins arm --host-name ${h.name} --collection ${collection}`], onLine);
    return play("uploaded", lines, onLine).then(() => arm({ host_name: h.name, collection }, onLine));
  }
  // plugins save: what the host runs now becomes what it starts with.
  function save({ host_name: name }, onLine) {
    const h = findHost(name);
    if (!h) return missingHost(name);
    const uploaded = STEP_NAMES.flatMap((step) => h.armed[step].filter((id) => h.uploads[`${step}/${id}`]).map((id) => `${step}/${id}`));
    if (uploaded.length) return { ok: false, kind: "error", text: `Cannot save: uploaded plugins cannot be saved (${uploaded.join(", ")}).` };
    return play("saved", [`Host: ${h.name}`,
      ...STEP_NAMES.map((step) => `${titleCase(step)}: ${selectionLabel(step, h.armed[step])} (${same(h.armed[step], STEPS[step].defaults) ? "default" : "saved"})`),
      `Activations: runtime/hosts/${h.name}/bundle/runtime`,
      `Startup: ${h.name} starts with these plugins; hosts update keeps the saved steps.`], onLine);
  }

  // Each route: viewer says the viewer answers it; wait is a read's latency (a status read probes).
  const ROUTES = {
    "GET /api/vehicles": { viewer: true, wait: 300, read: vehicles },
    "GET /api/simulators": { viewer: true, wait: 300, read: simulators },
    "POST /api/vehicles/discover": { viewer: true, write: discover },
    "POST /api/vehicles/remove": { viewer: true, write: untrack },
    "POST /api/simulators/create": { viewer: true, write: createSimulator },
    "POST /api/simulators/delete": { viewer: true, write: deleteSimulator },
    "GET /api/hosts/status": { viewer: true, wait: 300, read: hosts },
    "POST /api/hosts/create": { viewer: true, write: createHost },
    "POST /api/hosts/update": { viewer: true, write: updateHost },
    "POST /api/hosts/delete": { viewer: true, write: deleteHost },
    "POST /api/hosts/run": { viewer: true, write: runHost },
    "POST /api/hosts/stop": { viewer: true, write: stopHost },
    "POST /api/hosts/restart": { viewer: true, write: restartHost },
    "GET /api/plugins/list": { viewer: true, wait: 0, read: plugins },
    "POST /api/plugins/arm": { viewer: true, write: arm },
    "POST /api/plugins/disarm": { viewer: true, write: disarm },
    "POST /api/plugins/upload": { viewer: true, write: upload },
    "POST /api/plugins/save": { viewer: true, write: save },
    "GET /api/plugins/stream": { viewer: true, wait: 0, read: stream },
    "GET /api/hosts/latest": { viewer: true, wait: 0, read: latest },
  };
  const routeOf = (method, route) => ROUTES[`${method} ${route.split("?")[0]}`];
  const answers = (method, route) => { const r = routeOf(method, route); return !!r && !(SERVED && r.viewer); };
  async function answer(method, route, body, onLine = () => {}) {
    const handler = routeOf(method, route);
    if (method === "GET") {
      await after(handler.wait);
      return copy(handler.read(Object.fromEntries(new URL(route, location.href).searchParams)));
    }
    return copy(await handler.write(copy(body || {}), onLine));
  }

  // The hosts' runs: one decision per tick until a counted run finishes.
  setInterval(() => {
    for (const h of store.hosts) {
      if (h.state !== "running") continue;
      h.decisions += 1;
      h.lastFrameAt = Date.now();
      if (h.count && h.decisions >= h.count) Object.assign(h, { state: "finished_run", mode: null });
    }
  }, ms(250) || 25);

  // ---- The page's mock-only parts: gap marks, the gaps window, end process. ----
  function markGap(el, gap) {
    if (gap) {
      el.dataset.gap = gap;
      el.dataset.gapN = String(GAP_KEYS.indexOf(gap) + 1);
      el.title = GAPS[gap];
    } else if (el.dataset.gap) {
      delete el.dataset.gap;
      delete el.dataset.gapN;
      el.removeAttribute("title");
    }
  }
  document.querySelectorAll("[data-gap]").forEach((el) => markGap(el, el.dataset.gap));
  $("gapList").innerHTML = GAP_KEYS.map((key, i) => `<p class="gap-item"><b>${i + 1}</b><span>${GAPS[key]}</span></p>`).join("");
  $("gapsFrame").querySelector("[data-mode]").textContent = "CLI signals this page lacks";
  $("gapsToggle").addEventListener("click", () => {
    const on = !document.body.classList.contains("show-gaps");
    document.body.classList.toggle("show-gaps", on);
    $("gapsToggle").setAttribute("aria-pressed", String(on));
    if (on) floatingFrames.gapsFrame.restore(); else floatingFrames.gapsFrame.close();
  });
  $("workbenchLink").addEventListener("click", (event) => event.preventDefault());
  // Served, the simulators are real: the store has none to end.
  $("endProcess").hidden = SERVED;
  // The selected simulator's process dies, to show an exited record.
  $("endProcess").addEventListener("click", () => {
    const s = store.simulators.find((x) => x.url === Simulators.state.selected);
    if (!s || s.state === "exited") return;
    Object.assign(s, { state: "exited", logTail: [
      `[headless] chase ready on ws://127.0.0.1:${s.port}/`,
      "[headless] browser disconnected: Target page, context or browser has been closed",
      "[headless] exiting with code 1",
    ] });
    readFor(Simulators.state, "/api/simulators").then(refresh);
  });
  function paint() {
    const W = Simulators.state;
    const s = store.simulators.find((x) => x.url === W.selected);
    $("endProcess").disabled = !s || s.state === "exited" || W.busy || W.reading;
  }

  return { ms, store, answers, answer, markGap, paint };
})();
