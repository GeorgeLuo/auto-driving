// Learning: how a host's memory beliefs and plugins change over its run. The viewer keeps each
// host's history (/api/hosts/learning, LearningHistory); the window reads the rows after the last it
// has, once a second while it is open. The list: hosts, the chosen host's beliefs and memory plugins
// under it. The pane: the host's beliefs as a board, or one belief's or plugin's charts. Every chart
// is chosen by the shape of the recorded data, never by a plugin or belief name. The charts are the
// Learning page's (PR 414): times are the host's frame clock, the window's right edge its latest frame.
"use strict";

const Learning = (() => {
  const READ_MS = typeof mock !== "undefined" ? mock.ms(1000) || 40 : 1000;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const MAX_ROWS = 2000;
  const FAMILY_MIN = 9;
  const MIN_CELL = 220;
  const GAP = 14;
  const M = { l: 44, r: 10, t: 8, b: 18 };
  // The charts' time window: since the memory last reset, the whole history, or the last minute.
  const WINDOWS = { reset: "SINCE RESET", all: "ALL", recent: "LAST 60S" };
  const NO_SOURCE = "No memory status from this host yet: run it from Hosts.";

  // W: the chosen host and belief or plugin (focus: {kind, id}), the charts' window, and the last read.
  const W = { host: null, focus: null, windowName: "reset", paused: false, live: false, error: null, lastRead: 0, reading: false };
  let store = emptyStore(null);
  let cursor = 0;
  let domain = null;
  let hoverT = null;
  let cursors = [];
  let lastChartsKey = "";

  const root = $("learningFrame");
  const pane = root.querySelector("[data-learning]");
  const metrics = pane.querySelector("[data-metrics]");
  const body = pane.querySelector("[data-charts]");
  const raw = pane.querySelector("[data-raw]");
  const dot = document.createElement("span");
  dot.className = "status-dot";
  dot.hidden = true;
  root.querySelector(".float-frame-title").append(dot);

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function svgEl(name, attributes, parent) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, value);
    if (parent) parent.append(node);
    return node;
  }

  function svgText(parent, x, y, text, anchor = "start") {
    const node = svgEl("text", { x, y, "text-anchor": anchor }, parent);
    node.textContent = text;
    return node;
  }

  function titled(node, text) {
    svgEl("title", {}, node).textContent = text;
    return node;
  }

  function note(parent, text) {
    parent.append(el("div", "empty", text));
  }

  function appendMetric(container, name, value) {
    container.append(el("dt", "", name), el("dd", "", value ?? "—"));
  }

  function formatValue(value) {
    if (value === null || value === undefined) return "—";
    if (typeof value === "number") {
      if (!Number.isFinite(value)) return String(value);
      return Math.abs(value) >= 1000 ? String(Math.round(value)) : String(Number(value.toPrecision(4)));
    }
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  function clip(text, length = 80) {
    const value = String(text);
    return value.length > length ? `${value.slice(0, length - 1)}…` : value;
  }

  function compact(value) {
    if (value === null || value === undefined) return "—";
    if (Array.isArray(value)) return `[${value.length}]`;
    if (typeof value === "object") return `${Object.keys(value).length} keys`;
    return formatValue(value);
  }

  function counted(count, noun) {
    return `${count} ${noun}${count === 1 ? "" : "s"}`;
  }

  // Seconds before the window's right edge, which is the host's latest frame.
  function relTime(t) {
    const seconds = (t - domain.x1) / 1000;
    if (Math.abs(seconds) < 0.05) return "now";
    if (Math.abs(seconds) >= 120) return `${(seconds / 60).toFixed(1)} min`;
    return `${seconds.toFixed(Math.abs(seconds) < 10 ? 1 : 0)} s`;
  }

  // ---- History store: rows merged from each payload after the cursor.

  function emptyStore(historyId) {
    return {
      historyId, now: null, available: false, host: null,
      beliefs: new Map(), plugins: new Map(), events: [], minT: null, maxT: null,
    };
  }

  function seen(t) {
    if (typeof t !== "number") return;
    store.minT = store.minT === null ? t : Math.min(store.minT, t);
    store.maxT = store.maxT === null ? t : Math.max(store.maxT, t);
  }

  function mergeTrack(map, id, incoming) {
    if (!map.has(id)) map.set(id, { current: null, series: new Map(), checks: [], trials: new Map(), droppedPaths: 0 });
    const track = map.get(id);
    track.current = incoming.current ?? null;
    track.droppedPaths = incoming.dropped_paths || 0;
    for (const [path, rows] of Object.entries(incoming.series || {})) {
      const list = track.series.get(path) || [];
      for (const row of rows) {
        list.push(row);
        seen(row[1]);
      }
      if (list.length > MAX_ROWS) list.splice(0, list.length - MAX_ROWS);
      track.series.set(path, list);
    }
    for (const check of incoming.checks || []) {
      track.checks.push(check);
      seen(check.t);
    }
    if (track.checks.length > MAX_ROWS) track.checks.splice(0, track.checks.length - MAX_ROWS);
    for (const trial of incoming.trials || []) {
      // An ended trial comes again under a later seq; keep its latest row.
      track.trials.delete(trial.trial_id);
      track.trials.set(trial.trial_id, trial);
    }
    for (const trialId of track.trials.keys()) {
      if (track.trials.size <= MAX_ROWS) break;
      track.trials.delete(trialId);
    }
  }

  function merge(data) {
    store.now = data.now_ms ?? store.now;
    seen(store.now);
    store.available = Boolean(data.available);
    store.host = data.host_name || store.host;
    for (const [id, incoming] of Object.entries(data.beliefs || {})) mergeTrack(store.beliefs, id, incoming);
    for (const [id, incoming] of Object.entries(data.plugins || {})) mergeTrack(store.plugins, id, incoming);
    for (const event of data.events || []) {
      store.events.push(event);
      seen(event.t);
    }
    if (store.events.length > MAX_ROWS) store.events.splice(0, store.events.length - MAX_ROWS);
    cursor = data.seq ?? cursor;
  }

  // ---- Window: times on the host's frame clock, rows after the last reset.

  function resetEvents() {
    return store.events.filter((event) => event.kind === "reset");
  }

  function windowDomain() {
    const now = store.now ?? store.maxT ?? 0;
    const resets = resetEvents();
    const reset = resets[resets.length - 1] || null;
    let x0 = reset ? reset.t : (store.minT ?? now);
    let x1 = now;
    let minSeq = reset ? reset.seq : 0;
    if (W.windowName === "all") {
      x0 = store.minT ?? now;
      x1 = Math.max(now, store.maxT ?? now);
      minSeq = 0;
    } else if (W.windowName === "recent") {
      x0 = now - 60000;
    }
    return { x0, x1: x1 > x0 ? x1 : x0 + 1000, minSeq, resets: resets.map((event) => event.seq) };
  }

  // The rows the window shows, led by the value in effect when it opens.
  function windowRows(rows) {
    const shown = [];
    let before = null;
    for (const row of rows || []) {
      if (row[0] < domain.minSeq || row[1] < domain.x0) {
        before = row;
        continue;
      }
      if (row[1] <= domain.x1) shown.push(row);
    }
    if (before && before[2] !== null && (!shown.length || shown[0][1] > domain.x0)) {
      shown.unshift([Math.max(before[0], domain.minSeq), domain.x0, before[2]]);
    }
    return shown;
  }

  function valueAt(rows, t) {
    let value = null;
    for (const row of rows) {
      if (row[1] > t) break;
      value = row[2];
    }
    return value;
  }

  // A reset between two rows breaks the line; the frame clock restarted.
  function broken(a, b) {
    return domain.resets.some((seq) => seq > a[0] && seq <= b[0]);
  }

  // Each series' value in effect at every change of any of them.
  function stepMerge(seriesList) {
    const changes = [];
    seriesList.forEach((rows, index) => rows.forEach((row) => changes.push([row[0], row[1], index, row[2]])));
    changes.sort((a, b) => a[0] - b[0]);
    const values = seriesList.map(() => null);
    const merged = [];
    for (const [seq, t, index, value] of changes) {
      values[index] = value;
      const last = merged[merged.length - 1];
      if (last && last[1] === t) last.splice(2, values.length, ...values);
      else merged.push([seq, t, ...values]);
    }
    return merged;
  }

  function scale(d0, d1, r0, r1) {
    const k = (r1 - r0) / ((d1 - d0) || 1);
    const map = (value) => r0 + (value - d0) * k;
    map.invert = (position) => d0 + (position - r0) / k;
    return map;
  }

  function extent(values) {
    const finite = values.filter((value) => typeof value === "number" && Number.isFinite(value));
    if (!finite.length) return [0, 1];
    const lo = finite.reduce((a, b) => Math.min(a, b));
    const hi = finite.reduce((a, b) => Math.max(a, b));
    const pad = lo === hi ? (Math.abs(lo) * 0.1 || 1) : (hi - lo) * 0.08;
    return [lo >= 0 ? Math.max(0, lo - pad) : lo - pad, hi + pad];
  }

  function stepPath(rows, x, y) {
    let d = "";
    let previous = null;
    for (const row of rows) {
      const px = x(row[1]).toFixed(1);
      const joined = previous !== null && !broken(previous, row);
      if (typeof row[2] !== "number") {
        if (joined) d += `H${px}`;
        previous = null;
        continue;
      }
      const py = y(row[2]).toFixed(1);
      d += joined ? `H${px}V${py}` : `M${px},${py}`;
      previous = row;
    }
    if (previous) d += `H${x(domain.x1).toFixed(1)}`;
    return d;
  }

  function bandPath(merged, x, y, bounds) {
    let d = "";
    merged.forEach((row, index) => {
      const range = bounds(row.slice(2));
      const next = merged[index + 1];
      const left = x(row[1]);
      const right = x(next ? next[1] : domain.x1);
      if (!range || right <= left) return;
      d += `M${left.toFixed(1)},${y(range[1]).toFixed(1)}H${right.toFixed(1)}` +
        `V${y(range[0]).toFixed(1)}H${left.toFixed(1)}Z`;
    });
    return d;
  }

  // ---- Charts. Every time chart shares one hover cursor and readout time.

  function hover(t) {
    hoverT = t;
    for (const update of cursors) update(t);
  }

  function timeChart(parent, title, { width, height = 96, yDomain = null, left = M.l, xLabels = true, marks = [], draw }) {
    const box = el("div", "chart");
    const head = el("div", "chart-head");
    const readout = el("span", "readout");
    head.append(el("span", "chart-title", title), readout);
    const svg = svgEl("svg", {
      class: "plot", viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": title,
    });
    box.append(head, svg);
    parent.append(box);
    const bottom = height - (xLabels ? M.b : 4);
    const x = scale(domain.x0, domain.x1, left, width - M.r);
    const y = yDomain ? scale(yDomain[0], yDomain[1], bottom, M.t) : null;
    if (y) {
      for (const value of [yDomain[0], (yDomain[0] + yDomain[1]) / 2, yDomain[1]]) {
        svgEl("line", { class: "grid", x1: left, x2: width - M.r, y1: y(value), y2: y(value) }, svg);
        svgText(svg, left - 4, y(value) + 3, formatValue(value), "end");
      }
    }
    if (xLabels) {
      for (let index = 0; index <= 4; index += 1) {
        const t = domain.x0 + (domain.x1 - domain.x0) * index / 4;
        svgText(svg, x(t), height - 5, relTime(t), index === 0 ? "start" : index === 4 ? "end" : "middle");
      }
    }
    const resets = resetEvents().map((event) => ({ t: event.t, kind: "reset", label: "reset: the frame clock went back or memory reset" }));
    for (const mark of [...resets, ...marks]) {
      if (mark.t < domain.x0 || mark.t > domain.x1) continue;
      titled(svgEl("line", { class: `marker ${mark.kind}`, x1: x(mark.t), x2: x(mark.t), y1: M.t, y2: bottom }, svg), mark.label);
    }
    const readAt = draw(svg, x, y) || (() => "");
    const line = svgEl("line", { class: "cursor", x1: 0, x2: 0, y1: M.t, y2: bottom, visibility: "hidden" }, svg);
    svg.addEventListener("pointermove", (event) => {
      const rect = svg.getBoundingClientRect();
      hover(x.invert((event.clientX - rect.left) * width / (rect.width || width)));
    });
    svg.addEventListener("pointerleave", () => hover(null));
    cursors.push((t) => {
      const shown = t !== null && t >= domain.x0 && t <= domain.x1;
      line.setAttribute("visibility", shown ? "visible" : "hidden");
      if (shown) {
        line.setAttribute("x1", x(t));
        line.setAttribute("x2", x(t));
      }
      const text = readAt(shown ? t : domain.x1);
      readout.textContent = shown && text ? `${text} @ ${relTime(t)}` : text;
    });
    return box;
  }

  // A step line, a p25–p75 band, or both, over the window.
  function seriesChart(parent, track, title, { line = null, lo = null, hi = null, width, height = 96, xLabels = true }) {
    const rows = windowRows(track.series.get(line));
    const low = lo ? windowRows(track.series.get(lo)) : [];
    const high = hi ? windowRows(track.series.get(hi)) : [];
    const values = [...rows, ...low, ...high].map((row) => row[2]);
    timeChart(parent, title, {
      width, height, xLabels, yDomain: extent(values),
      draw(svg, x, y) {
        if (lo && hi) {
          const bounds = ([a, b]) => typeof a === "number" && typeof b === "number" ? [a, b] : null;
          svgEl("path", { class: "band", d: bandPath(stepMerge([low, high]), x, y, bounds) }, svg);
        }
        if (line) svgEl("path", { class: "line", d: stepPath(rows, x, y) }, svg);
        return (t) => {
          const value = line ? formatValue(valueAt(rows, t)) : "";
          const range = lo && hi ? `[${formatValue(valueAt(low, t))}, ${formatValue(valueAt(high, t))}]` : "";
          return [value, range].filter(Boolean).join(" ");
        };
      },
    });
  }

  function multiples(parent, width) {
    const columns = Math.max(1, Math.floor((width + GAP) / (MIN_CELL + GAP)));
    const cell = Math.floor((width - GAP * (columns - 1)) / columns);
    const grid = el("div", "multiples");
    grid.style.gridTemplateColumns = `repeat(${columns}, ${cell}px)`;
    parent.append(grid);
    return { grid, cell };
  }

  // Confidence (s+1)/(n+2) is the mean of Beta(s+1, c+1); its band is the
  // normal approximation of that distribution's 90% interval.
  function confidenceChart(parent, track, width) {
    const confidence = windowRows(track.series.get("/confidence"));
    const counts = windowRows(track.series.get("/checks#len"));
    const discards = [];
    for (const [path, rows] of track.series) {
      if (!path.startsWith("/discards/")) continue;
      let previous = 0;
      for (const row of rows) {
        const value = typeof row[2] === "number" ? row[2] : 0;
        if (row[0] >= domain.minSeq && value > previous) {
          discards.push({ t: row[1], kind: "discard", label: `discarded ${value - previous} checks: ${path.slice(10)}` });
        }
        previous = value;
      }
    }
    timeChart(parent, "confidence · 90% interval", {
      width, height: 110, yDomain: [0, 1], marks: discards,
      draw(svg, x, y) {
        svgEl("line", { class: "ref", x1: x(domain.x0), x2: x(domain.x1), y1: y(0.5), y2: y(0.5) }, svg);
        const bounds = ([mean, n]) => {
          if (typeof mean !== "number" || typeof n !== "number") return null;
          const half = 1.645 * Math.sqrt(mean * (1 - mean) / (n + 3));
          return [Math.max(0, mean - half), Math.min(1, mean + half)];
        };
        svgEl("path", { class: "band", d: bandPath(stepMerge([confidence, counts]), x, y, bounds) }, svg);
        svgEl("path", { class: "line", d: stepPath(confidence, x, y) }, svg);
        return (t) => {
          const mean = valueAt(confidence, t);
          return typeof mean === "number" ? `${mean.toFixed(3)} · n ${valueAt(counts, t) ?? 0}` : "no checks";
        };
      },
    });
  }

  function checkTime(check) {
    return typeof check.at_ms === "number" ? check.at_ms : check.t;
  }

  function windowChecks(track) {
    return track.checks.filter((check) => check.seq >= domain.minSeq
      && checkTime(check) >= domain.x0 && checkTime(check) <= domain.x1);
  }

  function methodsOf(items) {
    return [...new Set(items.map((item) => String(item.method)))].sort();
  }

  function outcomeClass(outcome) {
    return outcome === "support" || outcome === "contradict" ? outcome : "resolved";
  }

  function rasterChart(parent, track, width) {
    const checks = windowChecks(track);
    if (!checks.length) return note(parent, "No checks in this window.");
    const methods = methodsOf(checks);
    timeChart(parent, "checks by method", {
      width, height: methods.length * 20 + M.t + M.b, left: 110,
      draw(svg, x) {
        methods.forEach((method, lane) => {
          const middle = M.t + lane * 20 + 10;
          svgText(svg, 4, middle + 3, clip(method, 16));
          for (const check of checks.filter((item) => String(item.method) === method)) {
            const px = x(checkTime(check));
            const group = svgEl("g", {}, svg);
            svgEl("rect", { class: "hit", x: px - 4, y: middle - 8, width: 8, height: 16 }, group);
            svgEl("rect", { class: `tick ${outcomeClass(check.outcome)}`, x: px - 1, y: middle - 6, width: 2, height: 12, rx: 1 }, group);
            titled(group, `${method} · ${check.outcome} @ ${relTime(checkTime(check))}\n` +
              `predicted ${clip(JSON.stringify(check.predicted))}\nobserved ${clip(JSON.stringify(check.observed))}`);
          }
        });
        return (t) => {
          const upTo = checks.filter((check) => checkTime(check) <= t);
          const support = upTo.filter((check) => check.outcome === "support").length;
          return `${support} support · ${upTo.length - support} contradict`;
        };
      },
    });
    legend(parent, ["support", "contradict"]);
  }

  function interval(predicted) {
    if (typeof predicted === "number") return [predicted, predicted];
    if (Array.isArray(predicted) && predicted.length === 2 && predicted.every((value) => typeof value === "number")) {
      return [Math.min(...predicted), Math.max(...predicted)];
    }
    return null;
  }

  // A number or [lo, hi] prediction with a number observed is drawn on one
  // axis per method; any other shape is listed.
  function residualCharts(parent, track, width) {
    const checks = windowChecks(track);
    if (!checks.length) return note(parent, "No checks in this window.");
    for (const method of methodsOf(checks)) {
      const own = checks.filter((check) => String(check.method) === method);
      const scored = own.filter((check) => interval(check.predicted) && typeof check.observed === "number");
      if (!scored.length) {
        checkTable(parent, method, own);
        continue;
      }
      const ranged = scored.filter((check) => interval(check.predicted)[1] > interval(check.predicted)[0]);
      const points = scored.filter((check) => !ranged.includes(check));
      const inside = ranged.filter((check) => {
        const [lo, hi] = interval(check.predicted);
        return check.observed >= lo && check.observed <= hi;
      }).length;
      const values = scored.flatMap((check) => [...interval(check.predicted), check.observed]);
      timeChart(parent, `${method}: predicted vs observed`, {
        width, height: 120, yDomain: extent(values),
        draw(svg, x, y) {
          for (const check of scored) {
            const [lo, hi] = interval(check.predicted);
            const px = x(checkTime(check));
            const group = svgEl("g", {}, svg);
            if (hi > lo) {
              svgEl("line", { class: "interval", x1: px, x2: px, y1: y(lo), y2: y(hi) }, group);
            } else {
              svgEl("line", { class: "residual", x1: px, x2: px, y1: y(lo), y2: y(check.observed) }, group);
              svgEl("circle", { class: "predicted", cx: px, cy: y(lo), r: 4 }, group);
            }
            svgEl("circle", { class: `observed ${outcomeClass(check.outcome)}`, cx: px, cy: y(check.observed), r: 4 }, group);
            titled(group, `${check.outcome} @ ${relTime(checkTime(check))}: predicted ` +
              `${clip(JSON.stringify(check.predicted))}, observed ${formatValue(check.observed)}`);
          }
          const coverage = ranged.length
            ? `coverage ${inside}/${ranged.length} (${Math.round(100 * inside / ranged.length)}%)` : "";
          const error = points.length
            ? `mean |error| ${formatValue(points.reduce((sum, check) => sum + Math.abs(check.observed - interval(check.predicted)[0]), 0) / points.length)}`
            : "";
          return () => [coverage, error].filter(Boolean).join(" · ");
        },
      });
      if (ranged.length) {
        parent.append(el("div", "note",
          "Coverage is the share of observations inside their predicted interval; a calibrated p25–p75 interval covers about half."));
      }
      if (own.length > scored.length) checkTable(parent, `${method} (other shapes)`, own.filter((check) => !scored.includes(check)));
    }
  }

  function checkTable(parent, title, checks) {
    const head = el("div", "chart-head");
    head.append(el("span", "chart-title", `${title}: latest checks`), el("span", "readout", `${checks.length} in window`));
    const table = el("div", "check-table");
    for (const check of checks.slice(-12).reverse()) {
      const row = el("div", "check-row");
      row.append(
        el("span", "", relTime(checkTime(check))),
        el("span", "", String(check.outcome)),
        el("span", "", `predicted ${clip(JSON.stringify(check.predicted), 60)}`),
        el("span", "", `observed ${clip(JSON.stringify(check.observed), 60)}`),
      );
      table.append(row);
    }
    parent.append(head, table);
  }

  function trialStatus(trial) {
    if (trial.end === "resolved") return outcomeClass(trial.outcome);
    return ["open", "withdrawn", "removed"].includes(trial.end) ? trial.end : "resolved";
  }

  function trialChart(parent, track, width) {
    const trials = [...track.trials.values()]
      .filter((trial) => trial.end === "open" || trial.seq >= domain.minSeq)
      .map((trial) => ({
        ...trial,
        start: typeof trial.started_at_ms === "number" ? trial.started_at_ms : trial.opened_t,
        stop: trial.ended_t ?? domain.x1,
      }))
      .filter((trial) => trial.stop >= domain.x0 && trial.start <= domain.x1);
    if (!trials.length) return note(parent, "No trials in this window.");
    // Overlapping trials of one method stack in up to six rows.
    const lanes = methodsOf(trials).map((method) => {
      const ends = [];
      const placed = trials.filter((trial) => String(trial.method) === method)
        .sort((a, b) => a.start - b.start)
        .map((trial) => {
          let row = ends.findIndex((end) => end <= trial.start);
          if (row < 0 && ends.length < 6) row = ends.push(trial.stop) - 1;
          if (row < 0) row = ends.indexOf(Math.min(...ends));
          ends[row] = Math.max(ends[row], trial.stop);
          return { trial, row };
        });
      return { method, placed, depth: Math.max(1, ends.length) };
    });
    const height = lanes.reduce((sum, lane) => sum + lane.depth * 10 + 8, 0) + M.t + M.b;
    timeChart(parent, "trials by method", {
      width, height, left: 110,
      draw(svg, x) {
        let top = M.t;
        for (const lane of lanes) {
          svgText(svg, 4, top + 11, clip(lane.method, 16));
          for (const { trial, row } of lane.placed) {
            const left = x(Math.max(trial.start, domain.x0));
            const right = x(Math.min(trial.stop, domain.x1));
            const status = trialStatus(trial);
            const bar = svgEl("rect", {
              class: `bar ${status}`, x: left, y: top + 4 + row * 10, width: Math.max(2, right - left), height: 8, rx: 2,
            }, svg);
            titled(bar, `${trial.trial_id} · ${status}${trial.reason ? ` (${trial.reason})` : ""}\n` +
              `predicted ${clip(JSON.stringify(trial.predicted))}\n` +
              `${relTime(trial.start)} → ${trial.ended_t === null || trial.ended_t === undefined ? "open" : relTime(trial.ended_t)}`);
          }
          top += lane.depth * 10 + 8;
        }
        return (t) => `${trials.filter((trial) => trial.start <= t
          && (trial.end === "open" ? trial.stop >= t : trial.stop > t)).length} open`;
      },
    });
    legend(parent, ["open", "support", "contradict", "resolved", "withdrawn", "removed"]
      .filter((status) => trials.some((trial) => trialStatus(trial) === status)));
  }

  function legend(parent, statuses) {
    const row = el("div", "legend");
    for (const status of statuses) {
      const key = el("span", "");
      key.append(el("i", `swatch ${status}`), el("span", "", status));
      row.append(key);
    }
    parent.append(row);
  }

  // Paths that differ in one segment, with more than 8 members, are a family
  // drawn as one profile, key against value. A family whose last segment
  // varies needs number keys; other names there are unlike fields.
  function families(paths) {
    const members = new Map();
    const patternsOf = (path) => {
      const parts = path.split("/");
      const patterns = [];
      for (let index = 1; index < parts.length; index += 1) {
        patterns.push([[...parts.slice(0, index), "*", ...parts.slice(index + 1)].join("/"), parts[index]]);
      }
      return patterns;
    };
    for (const path of paths) {
      for (const [pattern, key] of patternsOf(path)) {
        if (!members.has(pattern)) members.set(pattern, []);
        members.get(pattern).push({ path, key: key.replace(/~1/g, "/").replace(/~0/g, "~") });
      }
    }
    const eligible = (pattern) => members.get(pattern).length >= FAMILY_MIN
      && (!pattern.endsWith("/*") || members.get(pattern).every((member) => isIndex(member.key)));
    const grouped = new Map();
    for (const path of paths) {
      let best = null;
      for (const [pattern] of patternsOf(path)) {
        if (eligible(pattern) && (!best || members.get(pattern).length > members.get(best).length)) best = pattern;
      }
      if (!best) continue;
      if (!grouped.has(best)) grouped.set(best, []);
      grouped.get(best).push(members.get(best).find((member) => member.path === path));
    }
    return grouped;
  }

  // A family's values by key now, faded at three earlier points in the
  // window, and at the hovered time.
  function profileChart(parent, track, title, { line = [], low = [], high = [] }, width) {
    const rowsOf = (members) => new Map(members.map((member) => [member.key, windowRows(track.series.get(member.path))]));
    const lines = rowsOf(line);
    const lows = rowsOf(low);
    const highs = rowsOf(high);
    const keys = [...new Set([...lines.keys(), ...lows.keys(), ...highs.keys()])];
    const numeric = keys.every(isIndex);
    keys.sort(numeric ? (a, b) => Number(a) - Number(b) : (a, b) => a.localeCompare(b));
    const height = 130;
    const bottom = height - M.b;
    const position = numeric
      ? scale(Number(keys[0]), Number(keys[keys.length - 1]), M.l + 6, width - M.r - 6)
      : scale(0, Math.max(1, keys.length - 1), M.l + 6, width - M.r - 6);
    const kx = (index) => position(numeric ? Number(keys[index]) : index);
    const at = (map, t) => keys.map((key) => (map.has(key) ? valueAt(map.get(key), t) : null));
    const past = [0.25, 0.5, 0.75].map((fraction) => domain.x0 + (domain.x1 - domain.x0) * fraction);
    const yDomain = extent([...past, domain.x1].flatMap((t) => at(lines, t)).concat(at(lows, domain.x1), at(highs, domain.x1)));
    const y = scale(yDomain[0], yDomain[1], bottom, M.t);
    const pathOf = (values) => {
      let d = "";
      let joined = false;
      values.forEach((value, index) => {
        if (typeof value !== "number") {
          joined = false;
          return;
        }
        d += `${joined ? "L" : "M"}${kx(index).toFixed(1)},${y(value).toFixed(1)}`;
        joined = true;
      });
      return d;
    };
    const counted = line.length ? lines : highs;
    const count = (t) => at(counted, t).filter((value) => typeof value === "number").length;

    const box = el("div", "chart");
    const head = el("div", "chart-head");
    const readout = el("span", "readout");
    head.append(el("span", "chart-title", title), readout);
    const svg = svgEl("svg", { class: "plot", viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": title });
    box.append(head, svg);
    parent.append(box);
    for (const value of [yDomain[0], (yDomain[0] + yDomain[1]) / 2, yDomain[1]]) {
      svgEl("line", { class: "grid", x1: M.l, x2: width - M.r, y1: y(value), y2: y(value) }, svg);
      svgText(svg, M.l - 4, y(value) + 3, formatValue(value), "end");
    }
    const step = Math.max(1, Math.ceil(keys.length / 8));
    keys.forEach((key, index) => {
      if (index % step === 0 || index === keys.length - 1) svgText(svg, kx(index), height - 5, clip(key, 10), "middle");
    });
    if (line.length) {
      past.forEach((t, index) => {
        svgEl("path", { class: "profile past", d: pathOf(at(lines, t)), "stroke-opacity": [0.2, 0.35, 0.5][index] }, svg);
      });
      const current = at(lines, domain.x1);
      svgEl("path", { class: "profile", d: pathOf(current) }, svg);
      current.forEach((value, index) => {
        if (typeof value !== "number") return;
        titled(svgEl("circle", { class: "dot", cx: kx(index), cy: y(value), r: 3 }, svg), `${keys[index]}: ${formatValue(value)}`);
      });
    }
    if (low.length && high.length) {
      const lo = at(lows, domain.x1);
      const hi = at(highs, domain.x1);
      keys.forEach((key, index) => {
        if (typeof lo[index] !== "number" || typeof hi[index] !== "number") return;
        titled(svgEl("line", { class: "interval", x1: kx(index), x2: kx(index), y1: y(lo[index]), y2: y(hi[index]) }, svg),
          `${key}: [${formatValue(lo[index])}, ${formatValue(hi[index])}]`);
      });
    }
    const hovered = svgEl("path", { class: "profile hover", d: "" }, svg);
    cursors.push((t) => {
      const shown = t !== null && t >= domain.x0 && t <= domain.x1;
      hovered.setAttribute("d", shown && line.length ? pathOf(at(lines, t)) : "");
      readout.textContent = shown
        ? `${count(t)} keys @ ${relTime(t)}`
        : `${count(domain.x1)} keys · ${count(domain.x0)} at window start`;
    });
  }

  function laneChart(parent, track, paths, width) {
    const lanes = paths.slice(0, 24).map((path) => ({ path, rows: windowRows(track.series.get(path)) }));
    const left = Math.min(220, Math.round(width * 0.35));
    timeChart(parent, "states", {
      width, height: lanes.length * 18 + M.t + M.b, left,
      draw(svg, x) {
        lanes.forEach(({ path, rows }, lane) => {
          const top = M.t + lane * 18;
          svgText(svg, 4, top + 12, clip(path, Math.floor(left / 6.2)));
          rows.forEach((row, index) => {
            if (typeof row[2] !== "string") return;
            const next = rows[index + 1];
            const start = x(row[1]);
            const span = Math.max(1, x(next ? next[1] : domain.x1) - start - 2);
            titled(svgEl("rect", { class: `lane-seg${index % 2 ? " alt" : ""}`, x: start, y: top + 2, width: span, height: 13, rx: 2 }, svg),
              `${path}: ${row[2]}`);
            if (span > 7 * Math.min(row[2].length, 24) + 6) {
              svgText(svg, start + 4, top + 12, clip(row[2], 24)).setAttribute("class", "lane-label");
            }
          });
        });
        return (t) => (lanes.length === 1 ? formatValue(valueAt(lanes[0].rows, t)) : "");
      },
    });
    if (paths.length > lanes.length) note(parent, `${paths.length - lanes.length} more state lanes not shown.`);
  }

  function isIndex(key) {
    return key !== "" && Number.isFinite(Number(key));
  }

  function leafKind(rows) {
    for (let index = (rows || []).length - 1; index >= 0; index -= 1) {
      if (rows[index][2] !== null) return typeof rows[index][2];
    }
    return null;
  }

  // Draws every path by its shape: families as profiles, p25/p75 siblings
  // as a band around their center, other numbers as small multiples, and
  // strings as state lanes. The lead path, or its family, comes first and
  // centers the first band.
  function renderPaths(parent, track, paths, width, { lead = null, centers = {} } = {}) {
    const numeric = paths.filter((path) => leafKind(track.series.get(path)) === "number");
    const strings = paths.filter((path) => leafKind(track.series.get(path)) === "string");
    if (!numeric.length && !strings.length) return note(parent, "Nothing recorded.");
    const grouped = families(numeric);
    const inFamily = new Set([...grouped.values()].flat().map((member) => member.path));
    const singles = numeric.filter((path) => !inFamily.has(path));
    const used = new Set();
    const charts = [];
    for (const path of singles) {
      const base = path.slice(0, -4);
      if (!path.endsWith("/p25") || !singles.includes(`${base}/p75`)) continue;
      const center = [centers[base], ...["median", "p50", "mean"].map((name) => `${base}/${name}`)]
        .find((candidate) => candidate && singles.includes(candidate)) || null;
      for (const member of [path, `${base}/p75`, center]) if (member) used.add(member);
      const band = `${base || "/"} p25–p75`;
      charts.push({ title: center ? `${center} · ${band}` : band, line: center, lo: path, hi: `${base}/p75`, lead: center !== null && center === lead });
    }
    if (lead && singles.includes(lead) && !used.has(lead)) {
      used.add(lead);
      charts.push({ title: lead, line: lead, lead: true });
    }
    charts.sort((a, b) => Number(b.lead) - Number(a.lead));
    const leadFamily = lead ? grouped.get(`${lead}/*`) || null : null;
    const profiles = [];
    for (const [pattern, members] of grouped) {
      const base = pattern.slice(0, -4);
      if (members === leadFamily || (pattern.endsWith("/p75") && grouped.has(`${base}/p25`))) continue;
      if (pattern.endsWith("/p25") && grouped.has(`${base}/p75`)) {
        const center = leadFamily && !profiles.some((profile) => profile.lead) ? leadFamily : null;
        profiles.push({
          title: center ? `${lead}/* · ${base} p25–p75` : `${base} p25–p75`,
          members: { line: center || [], low: members, high: grouped.get(`${base}/p75`) },
          lead: center !== null,
        });
      } else {
        profiles.push({ title: pattern, members: { line: members }, lead: false });
      }
    }
    if (leadFamily && !profiles.some((profile) => profile.lead)) {
      profiles.push({ title: `${lead}/*`, members: { line: leadFamily }, lead: true });
    }
    for (const profile of profiles.filter((item) => item.lead)) profileChart(parent, track, profile.title, profile.members, width);
    for (const chart of charts) seriesChart(parent, track, chart.title, { ...chart, width, height: chart.lead ? 120 : 96 });
    const rest = singles.filter((path) => !used.has(path));
    if (rest.length) {
      const { grid, cell } = multiples(parent, width);
      for (const path of rest) seriesChart(grid, track, path, { line: path, width: cell, height: 64, xLabels: false });
    }
    for (const profile of profiles.filter((item) => !item.lead)) profileChart(parent, track, profile.title, profile.members, width);
    if (strings.length) laneChart(parent, track, strings, width);
  }

  // ---- Page sections.

  function tally(counts) {
    let total = 0;
    const parts = [];
    for (const [method, reasons] of Object.entries(counts || {})) {
      for (const [reason, count] of Object.entries(reasons || {})) {
        total += Number(count) || 0;
        parts.push(`${method}/${reason} ${count}`);
      }
    }
    return { total, text: parts.join("\n") };
  }

  function sparkline(track) {
    const svg = svgEl("svg", { class: "plot spark", viewBox: "0 0 100 20", width: 100, height: 20, role: "img", "aria-label": "confidence over the window" });
    const x = scale(domain.x0, domain.x1, 1, 99);
    const y = scale(0, 1, 19, 1);
    svgEl("line", { class: "ref", x1: 1, x2: 99, y1: y(0.5), y2: y(0.5) }, svg);
    svgEl("path", { class: "line thin", d: stepPath(windowRows(track.series.get("/confidence")), x, y) }, svg);
    return svg;
  }

  // ---- The window: the hosts, the chosen host's beliefs and plugins, and the reads. ----

  const trackOf = (focus) => (focus ? (focus.kind === "belief" ? store.beliefs : store.plugins).get(focus.id) : null);
  const same = (a, b) => !!a && !!b && a.kind === b.kind && a.id === b.id;

  // A host's history starts empty here and is read whole: the viewer keeps it.
  function forget(host) {
    Object.assign(W, { host, focus: null, live: false, error: null, lastRead: 0 });
    store = emptyStore(null);
    cursor = 0;
    lastChartsKey = "";
  }
  function choose(host) {
    if (host === W.host) W.focus = null; else forget(host);
    render();
  }
  function focusOn(focus) {
    W.focus = same(focus, W.focus) ? null : focus;
    render();
  }

  // Reads the rows after the cursor; a new history (the viewer or the run restarted) is read whole.
  async function poll() {
    if (!W.host || W.paused || W.reading || root.hidden || Date.now() - W.lastRead < READ_MS) return;
    const host = W.host;
    W.reading = true;
    W.lastRead = Date.now();
    try {
      let doc = await api.read(routes.learning(host, cursor), { keep: false });
      if (host !== W.host) return;
      if (doc.ok !== false && doc.history_id !== store.historyId) {
        if (cursor !== 0) doc = await api.read(routes.learning(host, 0), { keep: false });
        store = emptyStore(doc.history_id);
        cursor = 0;
      }
      if (doc.ok === false) {
        Object.assign(W, { live: false, error: doc.error });
        return;
      }
      merge(doc);
      Object.assign(W, { live: !!doc.live, error: doc.error || null });
    } finally {
      W.reading = false;
    }
    render();
  }

  // The board: each belief's value, confidence and checks, then the memory plugins.
  function boardText() {
    const ids = [...store.beliefs.keys()].sort();
    const head = W.live ? "Live" : W.error ? `Not live: ${W.error}` : "Reading...";
    const lines = [`${head} | ${counted(store.beliefs.size, "belief")} · ${counted(store.plugins.size, "plugin")}`, ""];
    if (!store.available) return [...lines, W.error && !store.historyId ? "" : NO_SOURCE].join("\n");
    if (!ids.length) lines.push("No beliefs published yet.");
    for (const id of ids) {
      const belief = store.beliefs.get(id).current;
      if (!belief) { lines.push(id, "  removed from memory", ""); continue; }
      const n = (belief.checks || []).length;
      const confidence = typeof belief.confidence === "number" ? `${belief.confidence.toFixed(3)} · n ${n}` : "no checks";
      const byMethod = Object.entries(belief.by_method || {}).map(([method, c]) => `${method} ${c.support}✓ ${c.contradict}✗`).join("  ") || "none";
      const age = typeof store.now === "number" ? `${((store.now - belief.updated_at_ms) / 1000).toFixed(1)} s` : "—";
      lines.push(id, `  value ${compact(belief.value)} · confidence ${confidence} · age ${age}`,
                 `  checks ${byMethod} · open trials ${(belief.trials || []).length} · corroborated ${belief.corroborated ? "yes" : "no"}`,
                 `  withdrawn ${tally(belief.withdrawals).total} · discarded ${tally(belief.discards).total} · from ${belief.source_plugin_id}`, "");
    }
    for (const [id, track] of store.plugins) {
      const r = track.current || {};
      lines.push(`plugin ${id}: ${r.update_count ?? "?"} updates · ${r.failure_count ?? 0} failures · last ${formatValue(r.last_duration_ms)} ms`);
    }
    return lines.join("\n");
  }

  function view() {
    const rows = Hosts.listing().flatMap((h) => {
      const chosen = h.host_name === W.host;
      const own = (kind, id, name) => ({ name, child: true, selected: same(W.focus, { kind, id }), action: () => focusOn({ kind, id }) });
      return [{ name: h.host_name, selected: chosen && !W.focus, action: () => choose(h.host_name) },
              ...(chosen ? [...[...store.beliefs.keys()].sort().map((id) => own("belief", id, id)),
                            ...[...store.plugins.keys()].map((id) => own("plugin", id, `plugin ${id}`))] : [])];
    });
    if (!W.host) return { rows, status: Hosts.listing().length ? "Choose a host to chart what its memory learns." : "No hosts. Create one in Hosts." };
    const pause = { label: W.paused ? "RESUME" : "PAUSE", action: () => { W.paused = !W.paused; render(); } };
    const charted = !!trackOf(W.focus);
    const windows = Object.entries(WINDOWS).map(([name, label]) => ({ label, disabled: W.windowName === name,
      action: () => { W.windowName = name; lastChartsKey = ""; render(); } }));
    return { mode: `${W.host}${W.focus ? ` · ${W.focus.id}` : ""}${W.paused ? " · paused" : ""}`, rows,
             feed: charted, status: charted ? "" : boardText(), keepScroll: true, keys: charted ? [...windows, pause] : [pause] };
  }

  function renderBelief(width, track) {
    const belief = track.current;
    const paths = [...track.series.keys()];
    const under = (...prefixes) => paths.filter((path) => prefixes.some((prefix) =>
      path === prefix || path.startsWith(`${prefix}/`) || path.startsWith(`${prefix}#`)));
    const section = (title, fill) => { body.append(el("h3", "", title)); fill(body); };
    const updated = belief ? relTime(belief.updated_at_ms) : "";
    appendMetric(metrics, "Source", belief ? belief.source_plugin_id : "removed from memory");
    appendMetric(metrics, "Value", belief ? clip(compact(belief.value), 60) : "—");
    appendMetric(metrics, "Confidence", belief && typeof belief.confidence === "number"
      ? `${belief.confidence.toFixed(3)} · n ${(belief.checks || []).length}` : "no checks");
    appendMetric(metrics, "Updated", belief ? (updated === "now" ? "now" : `${updated.replace(/^-/, "")} ago`) : "—");
    section("Value", (parent) => renderPaths(parent, track, under("/value", "/properties"), width,
      { lead: "/value", centers: { "/properties": "/value" } }));
    section("Confidence", (parent) => confidenceChart(parent, track, width));
    section("Checks", (parent) => rasterChart(parent, track, width));
    section("Predicted vs observed", (parent) => residualCharts(parent, track, width));
    section("Trials", (parent) => trialChart(parent, track, width));
    section("Counts", (parent) => renderPaths(parent, track, under("/checks", "/trials", "/by_method", "/corroborated"), width));
    section("Withdrawn trials and discarded checks", (parent) => renderPaths(parent, track, under("/withdrawals", "/discards"), width));
    if (track.droppedPaths) note(body, `${track.droppedPaths} more paths were not recorded.`);
    raw.textContent = JSON.stringify(belief, null, 2);
  }
  function renderPlugin(width, track) {
    const record = track.current || {};
    appendMetric(metrics, "Spec", clip(formatValue(record.plugin_spec), 60));
    appendMetric(metrics, "Updates", formatValue(record.update_count));
    appendMetric(metrics, "Resets", formatValue(record.reset_count));
    appendMetric(metrics, "Failures", formatValue(record.failure_count));
    appendMetric(metrics, "Last update", record.last_duration_ms === null || record.last_duration_ms === undefined
      ? "—" : `${formatValue(record.last_duration_ms)} ms`);
    appendMetric(metrics, "Last error", record.last_error ? clip(record.last_error, 120) : "none");
    appendMetric(metrics, "Series", `${track.series.size}${track.droppedPaths ? ` (${track.droppedPaths} not recorded)` : ""}`);
    renderPaths(body, track, [...track.series.keys()], width);
    raw.textContent = JSON.stringify(record, null, 2);
  }
  // Charts are drawn again only when what they show changes: new rows, the window, the focus, the width.
  function renderCharts() {
    domain = windowDomain();
    const track = trackOf(W.focus);
    const width = Math.max(240, Math.floor(pane.clientWidth) - 24);
    const key = [store.historyId, cursor, store.now, W.windowName, W.focus.kind, W.focus.id, width].join("\0");
    if (key === lastChartsKey) return;
    lastChartsKey = key;
    cursors = [];
    metrics.replaceChildren();
    body.replaceChildren();
    if (W.focus.kind === "belief") renderBelief(width, track); else renderPlugin(width, track);
    hover(hoverT);
  }

  const w = deskWindow(root, view);
  function paint() {
    // A deleted host's window goes back to the hosts.
    if (W.host && !Hosts.find(W.host)) forget(null);
    const v = paintWindow(w);
    dot.hidden = !W.host;
    dot.classList.toggle("live", W.live && !W.paused);
    pane.hidden = !v.feed;
    if (v.feed && !root.hidden) renderCharts();
  }

  return { state: W, paint, poll };
})();
