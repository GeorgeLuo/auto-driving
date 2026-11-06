// Stream: one window per click, each on its own host and step. FEEDS lists the hosts, a host's
// steps under the one opened; choosing a step streams it. Perception shows the latest frame
// (Feed); the other steps show plugins stream --step's screen. PLGS opens Plugins on the
// stream's host and step.
"use strict";

const Stream = (() => {
  const STEPS = ["perception", "memory", "proposal", "plan", "action"];  // plugins stream --step
  const windows = [];
  let opened = 0;

  // A host's status and what it streams, for the feeds pane.
  const feedLines = (h) => [...Hosts.lines(h), "", ...STEPS.map((step) => `${titleCase(step)}: ${Plugins.armed(h.host_name, step).join(", ") || "(none)"}`)];

  // plugins stream --step's terminal screen for a host's latest publication; the CLI redraws it
  // in place, as the pane does.
  function screen(step, h, latest) {
    const frame = latest && latest.frame ? latest : null;
    const st = frame && frame.stream;
    const name = h.host_name;
    const armed = (s) => Plugins.armed(name, s);
    if (step === "perception") {
      // The recorded text, keeping only what the armed plugins produced.
      const on = armed("perception");
      const text = st ? st.perception_text.split("\n").filter((line) => {
        const m = line.match(/^(?:plugin_run id|signal id=\S+ plugin|thing id=\S+ plugin)=(\S+)/);
        return !m || on.includes(m[1]);
      }).map((line) => (line.startsWith("plugins=") ? `plugins=${on.join(",")}` : line)).join("\n") : "";
      return ["automa perception stream", "", `host: ${name}`, `url: ${h.url}`, `status: ${frame ? "live" : "waiting"}`,
        `presets: perception=${Plugins.defaultPreset("perception")} memory=${Plugins.defaultPreset("memory")}  mode: ${st ? st.mode : "-"}`,
        `control: steering=${st ? st.control.steering : "-"}  throttle=${st ? st.control.throttle : "-"}  reason=${st ? st.control.reason : "-"}`,
        `cadence: processed=${frame ? frame.decision_count : "-"}  cycle_ms=${st ? st.cycle_ms : "-"}  age_ms=${frame ? 4 : "-"}`,
        `latest: frame=${frame ? frame.frame.frame_id : "none"}  captured_at_ms=${frame ? frame.frame.captured_at_ms : "-"}  signals=${st ? st.signal_count : "-"}  things=${frame ? frame.perception.things.length : "-"}`,
        "view: http://127.0.0.1:8996/", `publication: ${frame ? "healthy" : "unavailable"}  json: /host/observation/latest  frame: /host/observation/latest/frame.jpg`,
        "", "latest perception", "-----------------", text || "(no latest perception yet)"].join("\n");
    }
    if (step === "memory") {
      const m = st && st.memory;
      return [`Live memory: ${name} [${frame ? "live" : "waiting"}]`, `Endpoint: ${h.url}/host/memory`,
        `Mode: ${st ? st.mode : "-"}  session: ${frame ? "running" : "idle"}`,
        ...(m ? [`Applied plugins: ${armed("memory").join(", ")}`,
                 ...m.plugins.map((x) => `  ${x.plugin_id}: health=${x.health} epoch=${x.epoch_id} records=${x.record_count}`),
                 `Evidence publisher: ${m.publisher || "none"}`, `Counters: updates=${frame.decision_count} resets=0 failures=0`] : []),
        "", "memory map: http://127.0.0.1:8996/memory", "perception view: http://127.0.0.1:8996/perception"].join("\n");
    }
    if (!st) return `Host ${name} has no fresh ${step} record yet.`;
    return [`${titleCase(step)} stream: ${name} frame=${frame.frame.frame_id} run=${st.run_id} age_ms=4 max_age_ms=2000`,
      `Generation: ${st.generation_id}`, `Plugins: ${armed(step).join(", ") || "none"}`, ...st[step],
      ...(step === "action" ? [st.application] : []),
      `Timing: ${step} 1 ms; cycle ${st.cycle_ms} ms`, "", "perception view: http://127.0.0.1:8996/perception"].join("\n");
  }

  // Given a step, a window opens streaming it; otherwise on its feeds.
  function open({ host = null, step = null } = {}) {
    const n = ++opened;
    const root = $("streamTemplate").content.firstElementChild.cloneNode(true);
    root.id = `streamFrame${n}`;
    addChrome(root);
    const dot = document.createElement("span");
    dot.className = "status-dot";
    dot.hidden = true;
    root.querySelector(".float-frame-title").append(dot);
    $("floatLayer").append(root);
    // Each opens a third of the page tall, right of the column and a step below the last.
    const column = document.querySelector("main").getBoundingClientRect();
    const height = Math.round(innerHeight / 3), width = 2 * height, down = ((n - 1) % 8) * 28;
    const x = Math.min(Math.max(8, innerWidth - width - 8), column.right + 16);
    localStorage.setItem(`automa-float-${root.id}`, JSON.stringify({ position: { x, y: height + down }, size: { width, height }, minimized: false, closed: false }));
    bindFloatingFrame(root);
    bindFrameStatus(root.querySelector(".float-frame-status"));
    const s = { root, dot, host, step, context: step ? "stream" : "feeds", open: host, paused: false, latest: null,
                meta: root.querySelector("[data-step-meta]"), aside: root.querySelector("[data-aside]"), feed: Feed.create(root) };
    s.w = deskWindow(root, () => view(s));
    // One click is one window: closing it ends it.
    root.querySelector("[data-frame-close]").addEventListener("click", () => {
      windows.splice(windows.indexOf(s), 1);
      localStorage.removeItem(`automa-float-${root.id}`);
      root.remove();
      render();
    });
    windows.push(s);
    bringUp(root.id);
    render();
  }
  // A started run's window: one already on the host's perception is brought forward.
  function openPerception(host) {
    const s = windows.find((x) => x.context === "stream" && x.host === host && x.step === "perception");
    if (s) bringUp(s.root.id); else open({ host, step: "perception" });
  }

  function view(s) {
    const plgs = { label: "PLGS", action: () => Plugins.show(s.host, s.step), disabled: s.context !== "stream" || Plugins.state.busy };
    if (s.context === "feeds") {
      const chosen = Hosts.find(s.open);
      const rows = Hosts.listing().flatMap((x) => [
        { name: x.host_name, selected: x === chosen, action: () => { s.open = s.open === x.host_name ? null : x.host_name; render(); } },
        ...(x === chosen ? STEPS.map((step) => ({ name: step, child: true,
          action: () => { Object.assign(s, { host: x.host_name, step, context: "stream", paused: false, latest: null }); render(); } })) : []),
      ]);
      // FEEDS goes back to the stream it left, when there is one.
      return { mode: "feeds", verbs: [{ label: "FEEDS", action: () => { s.context = "stream"; render(); }, disabled: !(s.host && s.step) }, plgs],
               rows, status: chosen ? feedLines(chosen).join("\n") : "", keepScroll: true, statusGap: "hostViews" };
    }
    const h = Hosts.find(s.host);
    const frame = s.latest && s.latest.frame ? s.latest : null;
    const perception = s.step === "perception";
    return {
      mode: `${s.host} · ${s.step}${frame ? ` · ${frame.frame.frame_id}` : ""}`,
      verbs: [{ label: "FEEDS", action: () => { Object.assign(s, { context: "feeds", open: s.host }); render(); } }, plgs],
      feed: perception,
      status: perception ? "" : screen(s.step, h, s.latest),
      keys: frame ? [{ label: s.paused ? "RESUME" : "PAUSE", action: () => { s.paused = !s.paused; render(); } }] : [],
      keepScroll: true, statusGap: "hostViews",
    };
  }

  // The other steps' details, where perception has its aside.
  function paintMeta(s) {
    const st = s.latest && s.latest.stream;
    const rows = [["Host", s.host], ["Step", s.step], ["Plugins", Plugins.armed(s.host, s.step).join(", ") || "none"],
                  ["Frame", st ? s.latest.frame.frame_id : "—"], ["Run", st ? st.run_id : "—"],
                  ["Generation", st ? st.generation_id : "—"], ["Cycle", st ? `${st.cycle_ms} ms` : "—"]];
    while (s.meta.children.length < rows.length * 2) s.meta.append(document.createElement("dt"), document.createElement("dd"));
    rows.forEach(([name, value], i) => {
      setText(s.meta.children[2 * i], name);
      setText(s.meta.children[2 * i + 1], value);
      if (s.meta.children[2 * i + 1].title !== value) s.meta.children[2 * i + 1].title = value;
    });
  }
  function paintOne(s) {
    // A deleted host's window goes back to its feeds.
    if (s.host && !Hosts.find(s.host)) Object.assign(s, { host: null, step: null, context: "feeds", latest: null });
    // A paused window keeps the publication it shows.
    if (s.context === "stream" && !s.paused) s.latest = api.docs[routes.latest(s.host)] || null;
    const v = paintWindow(s.w);
    const streaming = s.context === "stream";
    const frame = streaming && s.latest && s.latest.frame ? s.latest : null;
    if (s.root.dataset.context !== s.context) s.root.dataset.context = s.context;
    s.dot.hidden = !frame;
    s.dot.classList.toggle("live", !s.paused);
    s.feed.pane.hidden = !v.feed;
    s.aside.hidden = !v.feed;
    s.meta.hidden = !streaming || !!v.feed;
    if (!s.meta.hidden) paintMeta(s);
    Feed.paint(s.feed, v.feed ? frame : null);
  }

  return {
    open, openPerception, screen, feedLines,
    paint: () => windows.forEach(paintOne),
    count: () => windows.length,
    // The hosts the windows stream and are not paused on, for the page's polling.
    streamed: () => windows.filter((s) => s.context === "stream" && !s.paused).map((s) => s.host),
  };
})();
