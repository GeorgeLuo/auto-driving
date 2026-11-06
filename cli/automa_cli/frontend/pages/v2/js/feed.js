// The perception feed: a host's latest published frame (GET /api/latest) with its findings drawn
// over it, and the v1 /perception page's aside: the overlay toggles, the frame's metrics, the armed
// plugins' runs, their findings, the raw document. Its toggles and rows grow as frames bring
// new kinds and findings, and are refilled after that.
"use strict";

const Feed = (() => {
  const PALETTE = ["#00d084", "#ffb000", "#ff5a5f", "#4ea5ff", "#d678ff", "#f46f36", "#12b8b0"];
  const colorFor = (value) => {
    let hash = 0;
    for (const char of value) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
    return PALETTE[Math.abs(hash) % PALETTE.length];
  };
  const confidenceText = (value, digits = 2) => (Number.isFinite(Number(value)) && value !== null ? Number(value).toFixed(digits) : "—");
  const kindOf = (thing) => String(thing.kind || "unknown");
  const drawable = (thing) => thing?.kind !== "sensor_frame" && thing?.location?.frame === "image" &&
    (Array.isArray(thing.location.polygon_xy_norm) || Array.isArray(thing.location.bbox_xyxy_norm));
  const METRICS = ["Frame", "Captured", "Resolution", "Overlay source", "Overlay lag", "Result age", "Cycle", "Perception",
                   "Confidence", "Status", "Memory keys", "Evidence publisher"];

  function create(root) {
    const in_ = (selector) => root.querySelector(selector);
    const f = { root, pane: in_("[data-feed]"), image: in_(".feed-stage img"), canvas: in_(".feed-stage canvas"),
                overlays: { regions: true, labels: true }, kinds: new Map(), frame: null };
    for (const name of METRICS) {
      const dt = document.createElement("dt"); dt.textContent = name;
      const dd = document.createElement("dd"); dd.dataset.metric = name;
      in_("[data-metrics]").append(dt, dd);
    }
    const asideRow = () => {
      const row = document.createElement("div");
      row.className = "aside-row";
      row.innerHTML = `<span class="swatch"></span><div></div><div></div>`;
      return row;
    };
    f.runs = slotList(in_("[data-runs]"), asideRow);
    f.findings = slotList(in_("[data-findings]"), asideRow);
    for (const [list, none] of [[f.runs, "No plugin run data"], [f.findings, "No findings"]]) {
      list.none = document.createElement("div");
      list.none.textContent = none;
      list.container.append(list.none);
    }
    root.querySelectorAll("[data-overlay]").forEach((toggle) => toggle.addEventListener("click", () => {
      const key = toggle.dataset.overlay;
      f.overlays[key] = !f.overlays[key];
      toggle.setAttribute("aria-pressed", String(f.overlays[key]));
      draw(f);
    }));
    in_("[data-kinds]").addEventListener("click", (event) => {
      const toggle = event.target.closest("[data-kind]");
      if (!toggle) return;
      const kind = toggle.dataset.kind;
      f.kinds.set(kind, !f.kinds.get(kind));
      toggle.setAttribute("aria-pressed", String(f.kinds.get(kind)));
      draw(f);
    });
    f.image.addEventListener("load", () => draw(f));
    return f;
  }

  // A toggle per kind the frames have drawn, in name order.
  function addKinds(f, things) {
    const container = f.root.querySelector("[data-kinds]");
    for (const kind of [...new Set(things.filter(drawable).map(kindOf))].sort()) {
      if (f.kinds.has(kind)) continue;
      f.kinds.set(kind, true);
      const toggle = makeButton("aside-toggle");
      toggle.dataset.kind = kind;
      toggle.setAttribute("aria-pressed", "true");
      toggle.innerHTML = `<span class="swatch" style="background:${colorFor(kind)}"></span><span></span>`;
      toggle.lastChild.textContent = kind;
      const later = [...container.children].find((el) => el.dataset.kind > kind);
      container.insertBefore(toggle, later || null);
    }
  }

  // The frame's findings over its image, as the toggles say.
  function draw(f) {
    const { image, canvas, frame } = f;
    if (!image.naturalWidth || !frame) return;
    const context = canvas.getContext("2d");
    canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    context.clearRect(0, 0, canvas.width, canvas.height);
    // Labels are drawn at the image's own size; keep them the window's 11 px on screen.
    const scale = canvas.width / Math.max(1, image.clientWidth);
    const occupied = [];
    for (const thing of frame.perception.things.filter(drawable)) {
      const kind = kindOf(thing);
      if (f.kinds.get(kind) === false) continue;
      const loc = thing.location;
      const polygon = Array.isArray(loc.polygon_xy_norm) && loc.polygon_xy_norm.length >= 3 ? loc.polygon_xy_norm : null;
      const box = Array.isArray(loc.bbox_xyxy_norm) ? loc.bbox_xyxy_norm : null;
      const [x1, y1, x2, y2] = box || [Math.min(...polygon.map((p) => p[0])), Math.min(...polygon.map((p) => p[1])),
                                       Math.max(...polygon.map((p) => p[0])), Math.max(...polygon.map((p) => p[1]))];
      const x = x1 * canvas.width, y = y1 * canvas.height, width = (x2 - x1) * canvas.width, height = (y2 - y1) * canvas.height;
      if (width <= 0 || height <= 0) continue;
      const color = colorFor(kind);
      if (f.overlays.regions) {
        context.fillStyle = `${color}22`; context.strokeStyle = color; context.lineWidth = Math.max(2, scale * 1.5);
        if (polygon) {
          context.beginPath();
          polygon.forEach(([px, py], i) => (i ? context.lineTo : context.moveTo).call(context, px * canvas.width, py * canvas.height));
          context.closePath(); context.fill(); context.stroke();
        } else { context.fillRect(x, y, width, height); context.strokeRect(x, y, width, height); }
      }
      if (f.overlays.labels) {
        const id = String(thing.thing_id || kind);
        const label = `${id.length > 22 ? `${id.slice(0, 19)}...` : id}  ${confidenceText(thing.confidence)}`;
        context.font = `${Math.round(11 * scale)}px Menlo, Consolas, monospace`;
        const lw = context.measureText(label).width + 8 * scale, lh = 15 * scale;
        const clampX = (v) => Math.max(0, Math.min(canvas.width - lw, v));
        const spot = [[clampX(x), y - lh - 3], [clampX(x + width - lw), y - lh - 3], [clampX(x), y + 3], [clampX(x), y + height + 3]]
          .map(([lx, ly]) => ({ x: lx, y: ly, w: lw, h: lh }))
          .find((r) => r.y >= 0 && r.y + r.h <= canvas.height &&
            !occupied.some((o) => !(r.x + r.w + 3 <= o.x || o.x + o.w + 3 <= r.x || r.y + r.h + 3 <= o.y || o.y + o.h + 3 <= r.y)));
        if (spot) {
          occupied.push(spot);
          context.fillStyle = color; context.fillRect(spot.x, spot.y, spot.w, spot.h);
          context.fillStyle = "#101214"; context.fillText(label, spot.x + 4 * scale, spot.y + 11 * scale);
        }
      }
    }
  }

  function paintRows(list, items, line) {
    fillSlots(list, items, (row, item) => {
      const [name, detail, status, mark] = line(item);
      const className = `aside-row ${status}`.trim();
      if (row.className !== className) row.className = className;
      if (mark) row.style.setProperty("--mark", mark); else row.style.removeProperty("--mark");
      setText(row.children[1], name);
      setText(row.children[2], detail);
    });
    list.none.hidden = items.length > 0;
  }

  // Paints the frame, or the waiting note when there is none yet.
  function paint(f, frame) {
    const in_ = (selector) => f.root.querySelector(selector);
    in_("[data-empty]").hidden = !!frame;
    in_(".feed-stage").hidden = !frame;
    f.frame = frame;
    if (!frame) return;
    f.pane.style.setProperty("--feed-h", `${f.pane.clientHeight}px`);
    if (f.image.getAttribute("src") !== frame.image) f.image.src = frame.image;
    else draw(f);
    const p = frame.perception;
    addKinds(f, p.things);
    const values = {
      Frame: frame.frame.frame_id, Captured: String(frame.frame.captured_at_ms),
      Resolution: `${frame.frame.width_px} x ${frame.frame.height_px}`, "Overlay source": frame.overlay.source_frame_id,
      "Overlay lag": `${frame.overlay.frame_lag} frames / ${frame.overlay.frame_lag_ms} ms`, "Result age": `${frame.overlay.result_age_ms} ms`,
      Cycle: `${frame.cycle.cycle_duration_ms ?? "?"} ms`, Perception: `${frame.cycle.perception_ms ?? "?"} ms`,
      Confidence: confidenceText(p.confidence), Status: p.status || "unknown",
      "Memory keys": (frame.memory.plugins || []).length ? frame.memory.plugins.map((m) => m.plugin_id).join(", ") : "no plugins",
      "Evidence publisher": frame.memory.evidence_publisher || "none",
    };
    f.root.querySelectorAll("[data-metrics] dd").forEach((dd) => {
      const value = values[dd.dataset.metric] ?? "—";
      setText(dd, value);
      if (dd.title !== value) dd.title = value;
    });
    paintRows(f.runs, p.plugin_runs, (run) => [run.plugin_id, `${run.status} | ${run.duration_ms ?? "?"} ms | ${run.thing_count ?? 0} findings`, run.status || ""]);
    paintRows(f.findings, p.things, (t) => [t.label || t.thing_id, `${t.kind} | ${t.location?.frame}:${t.location?.zone} | ${confidenceText(t.confidence)}`, "", colorFor(kindOf(t))]);
    if (in_("details").open) setText(in_("[data-raw]"), JSON.stringify(frame, null, 2));
  }

  return { create, paint };
})();
