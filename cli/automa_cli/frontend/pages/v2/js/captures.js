// Captures: every recorded run under the runtime root, newest first. A row's details fill the
// pane. A closed run opens in the stream window's feed, one recorded frame at a time.
"use strict";

const Captures = (() => {
  const W = { mode: "list", selected: null, index: 0, doc: null, meta: null, fault: "", reading: false, reads: 0 };
  const root = $("capturesFrame");
  const feed = Feed.create(root);
  const listing = () => (api.docs["/api/captures"] || { captures: [] }).captures;
  const find = (id) => listing().find((c) => c.capture === id);
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = (ms) => {
    if (!Number.isInteger(ms)) return "—";
    const d = new Date(ms);
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };
  const duration = (ms) => {
    if (!Number.isInteger(ms) || ms < 0) return "—";
    if (ms < 1000) return `${ms} ms`;
    const seconds = ms / 1000;
    if (seconds < 60) return `${seconds.toFixed(1)} s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes} m ${Math.round(seconds % 60)} s`;
  };
  const bytes = (n) => {
    if (!Number.isInteger(n)) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
    return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  };
  const rowName = (c) => [c.host, c.run_id, stamp(c.started_at_ms), duration(c.duration_ms), `${c.frame_count} frames`, bytes(c.size_bytes), c.state].join("  ");

  function metadataText(m) {
    if (!m) return W.fault || (W.reading ? "..." : "");
    const release = m.release && m.release.tree_sha256;
    const sources = m.sources || [];
    const lines = [
      `Run ${m.run_id}`,
      `Host ${m.host}`,
      `Vehicle ${m.vehicle || "—"}`,
      `Format ${m.format === "entry" ? "entry file" : "inline"}`,
      `Frames ${m.frame_count}, ${m.flagged_frames} with an error or absence reason`,
      `First ${stamp(m.started_at_ms)}`,
      `Last ${stamp(m.ended_at_ms)}`,
      `Release ${release || "—"}`,
      sources.length ? `Uploaded sources ${sources.map((s) => `${s.name} ${bytes(s.bytes)}`).join(", ")}` : "Uploaded sources none",
    ];
    for (const gen of m.generations || []) {
      const tree = gen.release && gen.release.tree_sha256;
      lines.push(`Generation ${gen.generation_id || "—"}${tree && tree !== release ? `  ${tree}` : ""}`);
      for (const step of gen.steps || []) lines.push(`  ${step.step}: ${(step.plugins || []).join(", ") || "(none)"}`);
    }
    lines.push("Files");
    for (const file of m.files || []) lines.push(`  ${file.name}  ${bytes(file.bytes)}  ${file.count}  largest ${file.largest} ${bytes(file.largest_bytes)}`);
    return lines.join("\n");
  }
  function commandText(doc) {
    const app = doc && doc.report && doc.report.application;
    if (!app || app.applied === false || (app.steering == null && app.throttle == null)) return "not applied";
    return `steer ${app.steering}  throttle ${app.throttle}`;
  }
  async function loadMeta(id, quiet) {
    const n = ++W.reads;
    if (!quiet) { W.reading = true; W.fault = ""; render(); }
    const doc = await api.read(routes.capture(id), { keep: false });
    if (n !== W.reads) return;
    W.reading = false;
    if (doc.ok === false) { W.meta = null; W.fault = doc.error || ""; }
    else { W.meta = doc; W.fault = ""; }
    render();
  }
  async function loadFrame() {
    const c = find(W.selected);
    if (!c || W.mode !== "view") return;
    const n = ++W.reads;
    const doc = await api.read(routes.captureFrame(c.capture, W.index), { keep: false });
    if (n !== W.reads || W.mode !== "view") return;
    W.doc = doc.ok === false ? null : doc;
    W.fault = doc.ok === false ? doc.error || "" : "";
    render();
  }
  function select(id) {
    if (W.selected === id && W.mode === "list") { W.selected = null; W.meta = null; W.fault = ""; render(); return; }
    Object.assign(W, { mode: "list", selected: id, doc: null, meta: null, fault: "" });
    return loadMeta(id);
  }
  function openView() {
    const c = find(W.selected);
    if (!c || c.state === "active" || !c.frame_count) return;
    Object.assign(W, { mode: "view", index: 0, doc: null, fault: "" });
    return loadFrame();
  }
  function step(to) {
    const c = find(W.selected);
    if (!c) return;
    const last = Math.max(0, c.frame_count - 1);
    const next = Math.max(0, Math.min(last, to));
    if (next === W.index && W.doc) { render(); return; }
    W.index = next;
    return loadFrame();
  }

  // Why the list is empty: the viewer refused it, or no host has recorded yet.
  function emptyText() {
    const fault = api.faults["/api/captures"];
    if (fault) return `The viewer did not list captures: ${fault}. A viewer started before the Captures window was added needs a restart.`;
    return listing().length ? "" : "No recordings yet. A host started with recording on writes one.";
  }

  function view() {
    const c = find(W.selected);
    if (W.mode === "view" && c) {
      const ready = W.doc && !W.fault;
      const time = stamp(W.doc && (W.doc.image && W.doc.image.captured_at_ms != null ? W.doc.image.captured_at_ms : W.doc.report && W.doc.report.timestamp_ms));
      const last = c.frame_count - 1;
      return {
        mode: ready ? `${W.index + 1}/${c.frame_count} · ${time} · ${commandText(W.doc)}` : "",
        feed: !!ready,
        status: W.fault || (ready ? "" : "..."),
        keys: [
          { label: "FIRST", action: () => step(0), disabled: W.index === 0 },
          { label: "PREV", action: () => step(W.index - 1), disabled: W.index === 0 },
          { label: "NEXT", action: () => step(W.index + 1), disabled: W.index >= last },
          { label: "LAST", action: () => step(last), disabled: W.index >= last },
          { label: "LIST", action: () => { W.mode = "list"; W.doc = null; W.fault = ""; render(); } },
        ],
      };
    }
    const viewable = c && c.state !== "active" && c.frame_count > 0;
    return {
      mode: "",
      rows: listing().map((x) => ({ name: rowName(x), selected: W.selected === x.capture, marked: x.state === "active", action: () => select(x.capture) })),
      status: c ? metadataText(W.meta) : emptyText(),
      keys: viewable ? [{ label: "VIEW", action: openView }] : [],
      fromTop: true,
    };
  }

  function paint() {
    const v = paintWindow(w);
    const viewing = W.mode === "view";
    if (root.dataset.context !== (viewing ? "view" : "list")) root.dataset.context = viewing ? "view" : "list";
    feed.pane.hidden = !v.feed;
    root.querySelector("[data-aside]").hidden = !v.feed;
    Feed.paint(feed, v.feed ? W.doc : null);
  }
  function poll() {
    const c = find(W.selected);
    if (W.mode === "list" && c && c.state === "active") return loadMeta(c.capture, true);
  }

  const w = deskWindow(root, view);
  return { state: W, listing, paint, poll };
})();
