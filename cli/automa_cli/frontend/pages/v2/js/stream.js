// Stream: one window per click, each on its own host and step. FEEDS lists the hosts, a host's
// steps under the one opened; choosing a step streams it. Perception shows the host's latest
// frame (Feed); the other steps show plugins stream --once's screen. PLGS opens Plugins on the
// stream's host and step.
"use strict";

const Stream = (() => {
  const STEPS = ["perception", "memory", "proposal", "plan", "action"];  // plugins stream --step
  const windows = [];
  let opened = 0;

  // A host's status and what it streams, for the feeds pane.
  const feedLines = (h) => [...Hosts.lines(h), "", ...STEPS.map((step) => `${titleCase(step)}: ${Plugins.armed(h.host_name, step).join(", ") || "(none)"}`)];
  // What a window reads: the host's latest frame for perception, the step's probe otherwise.
  const routeOf = (host, step) => (step === "perception" ? routes.latest(host) : routes.stream(host, step));
  // The frame a window's document names, when it has one.
  const frameOf = (s) => (!s.doc ? null : s.step === "perception" ? (s.doc.report ? s.doc.image.frame_id : null) : s.doc.frame_id || null);

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
    const s = { root, dot, host, step, context: step ? "stream" : "feeds", open: host, paused: false, doc: null,
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
          action: () => { Object.assign(s, { host: x.host_name, step, context: "stream", paused: false, doc: null }); render(); } })) : []),
      ]);
      // FEEDS goes back to the stream it left, when there is one.
      return { mode: "feeds", verbs: [{ label: "FEEDS", action: () => { s.context = "stream"; render(); }, disabled: !(s.host && s.step) }, plgs],
               rows, status: chosen ? feedLines(chosen).join("\n") : "", keepScroll: true };
    }
    const frame = frameOf(s);
    const perception = s.step === "perception";
    return {
      mode: `${s.host} · ${s.step}${frame ? ` · ${frame}` : ""}`,
      verbs: [{ label: "FEEDS", action: () => { Object.assign(s, { context: "feeds", open: s.host }); render(); } }, plgs],
      feed: perception,
      status: perception ? "" : s.doc ? s.doc.text : `Reading ${s.step} on ${s.host}...`,
      keys: frame ? [{ label: s.paused ? "RESUME" : "PAUSE", action: () => { s.paused = !s.paused; render(); } }] : [],
      keepScroll: true,
    };
  }

  // The other steps' details, from their probe, where perception has its aside.
  function paintMeta(s) {
    const p = s.doc || {};
    const cycle = p.timing?.duration_ms;
    const rows = [["Host", s.host], ["Step", s.step], ["Status", p.status || "—"],
                  ["Plugins", (p.plugin_ids || p.plugins || []).join(", ") || "none"], ["Frame", p.frame_id || "—"],
                  ["Run", p.run_id || "—"], ["Generation", p.generation_id || "—"],
                  ["Cycle", Number.isFinite(cycle) ? `${cycle.toFixed(1)} ms` : "—"]];
    while (s.meta.children.length < rows.length * 2) s.meta.append(document.createElement("dt"), document.createElement("dd"));
    rows.forEach(([name, value], i) => {
      setText(s.meta.children[2 * i], name);
      setText(s.meta.children[2 * i + 1], value);
      if (s.meta.children[2 * i + 1].title !== value) s.meta.children[2 * i + 1].title = value;
    });
  }
  function paintOne(s) {
    // A deleted host's window goes back to its feeds.
    if (s.host && !Hosts.find(s.host)) Object.assign(s, { host: null, step: null, context: "feeds", doc: null });
    // A paused window keeps the document it shows.
    if (s.context === "stream" && !s.paused) s.doc = api.docs[routeOf(s.host, s.step)] || null;
    const v = paintWindow(s.w);
    const streaming = s.context === "stream";
    const frame = streaming ? frameOf(s) : null;
    if (s.root.dataset.context !== s.context) s.root.dataset.context = s.context;
    s.dot.hidden = !frame;
    // Live while the host publishes fresh frames; a stopped host's last frame is stale.
    const fresh = s.step === "perception" ? s.doc?.health !== "stale" : s.doc?.status === "live";
    s.dot.classList.toggle("live", !s.paused && fresh);
    s.feed.pane.hidden = !v.feed;
    s.aside.hidden = !v.feed;
    s.meta.hidden = !streaming || !!v.feed;
    if (!s.meta.hidden) paintMeta(s);
    Feed.paint(s.feed, v.feed && streaming ? s.doc : null);
  }

  return {
    open, openPerception, feedLines, routeOf,
    paint: () => windows.forEach(paintOne),
    count: () => windows.length,
    // The reads of the windows that stream and are not paused, for the page's polling.
    streamed: () => windows.filter((s) => s.context === "stream" && !s.paused).map((s) => routeOf(s.host, s.step)),
  };
})();
