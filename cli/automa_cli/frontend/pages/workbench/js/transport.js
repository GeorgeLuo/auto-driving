// Transport region: cadence, loop, start/pause/resume/step/reset, scrub and seek, and the action call.
// Classic script: the page's files share one global scope.
"use strict";

var cadenceDraft = false;
var scrubbing = false;
var resumeAfterScrub = false;
var pendingResume = false;
var scrubPosition = null;
var seekQueued = null;
var seekInFlight = false;
var seekRequestId = 0;
var seekTarget = null;
var loopDraft = null;
var pendingLoop = null;
var loopInFlight = false;
var statePollGeneration = 0;
function beginStateRequest(action) {
  var exclusive = action && !playbackControlAction(action) && action !== "set_loop";
  if (exclusive) stateRequestGeneration += 1;
  if (action) statePollGeneration += 1;
  return {
    action: action,
    exclusive: exclusive,
    generation: stateRequestGeneration,
    pollGeneration: statePollGeneration,
    runId: (state && state.run_id) || null
  };
}
function stateRequestIsCurrent(request, next) {
  if (request.generation !== stateRequestGeneration) return false;
  if (!request.action) return request.pollGeneration === statePollGeneration;
  var currentRun = (state && state.run_id) || null;
  if (request.action === "start" || request.action === "reset") {
    return currentRun === request.runId || (next && currentRun === (next.run_id || null));
  }
  return currentRun === request.runId && (!next || (next.run_id || null) === request.runId);
}
function acceptStateResponse(request, next) {
  if (!stateRequestIsCurrent(request, next)) return false;
  if (request.action) {
    // Invalidate earlier polls and controls when a frame-boundary action commits.
    // Lightweight controls must leave a pending plugin selection current.
    statePollGeneration += 1;
    if (request.exclusive) request.generation = ++stateRequestGeneration;
  }
  return true;
}
function parseCadence(raw) {
  var value = String(raw || "").trim().toLowerCase();
  if (value === "realtime" || value === "real") return { pace: "realtime" };
  if (!value) return null;
  var ms = Number(value);
  if (!Number.isFinite(ms) || ms < 0) return null;
  return { pace: "fixed", cadence_ms: Math.round(ms) };
}
function cadencePayload() {
  return parseCadence(elements.cadence.value);
}
function loopEnabled() {
  if (loopDraft !== null) return loopDraft;
  return elements.loopToggle.getAttribute("aria-pressed") === "true";
}
function paintLoopToggle(on) {
  elements.loopToggle.value = on ? "on" : "off";
  elements.loopToggle.setAttribute("aria-pressed", on ? "true" : "false");
}
function queueLoop(on) {
  loopDraft = on;
  paintLoopToggle(on);
  pendingLoop = on;
  flushLoop();
}
function flushLoop() {
  if (pendingLoop === null || loopInFlight) return;
  var on = pendingLoop;
  pendingLoop = null;
  loopInFlight = true;
  var request = beginStateRequest("set_loop");
  var body = { action: "set_loop", loop: on };
  var runId = selectedRunId("set_loop");
  if (runId) body.run_id = runId;
  fetch("api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  }).then(function (response) {
    return response.json().then(function (payload) {
      return { ok: response.ok, payload: payload };
    }, function () {
      return { ok: response.ok, payload: null };
    });
  }).then(function (result) {
    loopInFlight = false;
    var next = result.payload && result.payload.state;
    if (!acceptStateResponse(request, next)) {
      if (pendingLoop !== null) flushLoop();
      return;
    }
    var controls = next && next.controls;
    if (controls && typeof controls.loop === "boolean") {
      if (state && state.controls) state.controls.loop = controls.loop;
      if (loopDraft === controls.loop) loopDraft = null;
      if (pendingLoop === null) {
        paintLoopToggle(loopDraft !== null ? loopDraft : controls.loop);
      }
    }
    if (!result.ok || (result.payload && result.payload.ok === false)) {
      setNotice((result.payload && result.payload.message) || "Action was rejected.");
      if (pendingLoop === null && controls && typeof controls.loop === "boolean") {
        loopDraft = null;
        paintLoopToggle(controls.loop);
      }
    }
    if (pendingLoop !== null) flushLoop();
  }).catch(function (error) {
    loopInFlight = false;
    if (stateRequestIsCurrent(request)) setNotice("Workbench connection failed: " + error);
    if (pendingLoop !== null) flushLoop();
  });
}
function playbackControlAction(name) {
  return name === "pause" || name === "resume" || name === "set_cadence";
}
var controlInFlight = false;
// Pause, resume, and cadence stay available while a plugin change waits out a
// slow frame. They must not cancel that change or take its in-flight flag.
async function controlAction(action, extra) {
  if (controlInFlight) return;
  controlInFlight = true;
  var request = beginStateRequest(action);
  var body = { action: action };
  var runId = selectedRunId(action);
  if (runId) body.run_id = runId;
  if (extra) Object.keys(extra).forEach(function (key) { body[key] = extra[key]; });
  try {
    var response = await fetch("api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    var payload = await response.json();
    if (!acceptStateResponse(request, payload.state)) return;
    if (!response.ok || payload.ok === false) {
      setNotice((payload && payload.message) || "Action was rejected.");
      return;
    }
    if (!payload.state) return;
    if (actionInFlight && state) {
      state.phase = payload.state.phase;
      state.controls = payload.state.controls;
      renderControls();
      return;
    }
    render(payload.state, { skipViewer: true });
  } catch (error) {
    if (stateRequestIsCurrent(request)) setNotice("Workbench connection failed: " + error);
  } finally {
    controlInFlight = false;
    flushSeek();
    flushResume();
  }
}
async function action(action, extra) {
  if (action === "resume" && seekInFlight) { pendingResume = true; return; }
  if (playbackControlAction(action)) return controlAction(action, extra);
  if (actionInFlight) return;
  var request = beginStateRequest(action);
  actionInFlight = true;
  var skipViewer = playbackControlAction(action);
  if (action === "reset" && seekInFlight) {
    seekRequestId += 1;
    seekInFlight = false;
    seekQueued = null;
    seekTarget = null;
    pendingResume = false;
  }
  if (["start", "validate", "reset"].indexOf(action) >= 0) clearRecordSelection();
  if (["start", "reset"].indexOf(action) >= 0) cadenceDraft = false;
  var body = { action: action };
  var runId = selectedRunId(action);
  if (runId) body.run_id = runId;
  if (extra) Object.keys(extra).forEach(function (key) { body[key] = extra[key]; });
  if (!skipViewer) setNotice("");
  if (action === "select_plugins") {
    setNotice(state && state.phase === "paused" ? "Refreshing frame…" : "Applying selection…", true);
  }
  try {
    var response = await fetch("api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    var payload = await response.json();
    if (!acceptStateResponse(request, payload.state)) return;
    if (payload.state) {
      actionInFlight = false;
      render(payload.state, { skipViewer: skipViewer });
    }
    if (!response.ok || payload.ok === false) {
      if (stateRequestIsCurrent(request, payload.state)) {
        if (action === "select_plugins") {
          revertPluginDraft(extra && extra.step);
          if (payload.state) render(payload.state);
        }
        setNotice(payload.message || "Action was rejected.");
      }
      return;
    }
    if (stateRequestIsCurrent(request, payload.state)) {
      if (action === "validate") setNotice("Source and plugin configuration validated.");
      if (action === "select_plugins") setNotice("Plugin selection applied.");
      if (action === "select_plugins") {
        clearRecordSelection();
        render(payload.state);
      }
      if (action === "select_plugins") settlePluginDraft(extra && extra.step);
      if (action === "start") settlePluginDraft();
    }
  } catch (error) {
    if (stateRequestIsCurrent(request)) {
      if (action === "select_plugins") {
        revertPluginDraft(extra && extra.step);
        render();
      }
      setNotice("Workbench connection failed: " + error);
    }
  } finally {
    actionInFlight = false;
    if (stateRequestIsCurrent(request)) renderControls();
    schedulePoll(pollDelay());
    flushSeek();
    flushResume();
    flushPluginSelection();
  }
}
function renderControls() {
  var allowed = state && state.controls && state.controls.allowed_actions
    ? state.controls.allowed_actions : [];
  var canStart = allowed.indexOf("start") >= 0;
  var canPause = allowed.indexOf("pause") >= 0;
  var canResume = allowed.indexOf("resume") >= 0;
  setButton("startButton", canStart);
  setButton("pauseButton", canPause);
  setButton("resumeButton", canResume);
  var playing = state && state.phase === "running";
  var paused = state && state.phase === "paused";
  elements.pauseButton.hidden = !playing;
  elements.resumeButton.hidden = !paused;
  elements.startButton.hidden = playing || paused || !canStart;
  setButton("stepButton", allowed.indexOf("step") >= 0);
  setButton("resetButton", allowed.indexOf("reset") >= 0);
  elements.cadence.disabled = !state;
  var liveReplay = Boolean(state) && (state.phase === "running" || state.phase === "paused");
  elements.sourceDir.disabled = liveReplay;
  var frame = currentFrame();
  var historical = liveReplay && frame && state.latest_processed_position != null &&
    frame.position !== state.latest_processed_position;
  elements.historicalSelection.hidden = !historical;
  elements.latestFrameButton.hidden = !historical;
  elements.latestFrameButton.disabled = actionInFlight;
  renderPlugins();
}
elements.latestFrameButton.addEventListener("click", function () {
  queueSeek(state.latest_processed_position);
});
elements.startButton.addEventListener("click", function () {
  var cadence = cadencePayload();
  if (!cadence) {
    setNotice("Cadence must be a millisecond delay or realtime.");
    return;
  }
  var payload = {
    source_dir: elements.sourceDir.value,
    pace: cadence.pace
  };
  if (cadence.pace !== "realtime") payload.cadence_ms = cadence.cadence_ms;
  payload.loop = loopEnabled();
  action("start", payload);
});
elements.pauseButton.addEventListener("click", function () { action("pause"); });
elements.resumeButton.addEventListener("click", function () { action("resume"); });
elements.stepButton.addEventListener("click", function () { action("step"); });
elements.resetButton.addEventListener("click", function () { action("reset"); });
elements.cadence.addEventListener("change", function () {
  cadenceDraft = state && state.phase === "idle";
  if (state && (state.phase === "running" || state.phase === "paused")) {
    cadenceDraft = false;
    var cadence = cadencePayload();
    if (!cadence) return;
    var payload = { pace: cadence.pace };
    if (cadence.pace !== "realtime") payload.cadence_ms = cadence.cadence_ms;
    action("set_cadence", payload);
  }
});
function showScrubImage(position) {
  var runId = state && state.run_id;
  if (!runId) return;
  var imageKey = text(runId, "") + ":pos:" + position;
  setText("frameIndex", position);
  setText("frameId", "seeking");
  setText("frameTimestamp", null);
  elements.overlayCanvas.width = 1;
  elements.overlayCanvas.height = 1;
  if (imageKey === lastImageKey) return;
  lastImageKey = imageKey;
  var requestGeneration = ++imageRequestGeneration;
  var imageUrl = "api/frame?run_id=" + encodeURIComponent(runId) + "&position=" + encodeURIComponent(position);
  var hasRenderedImage = loadedImageKey !== "";
  if (!hasRenderedImage) {
    elements.viewerFrame.hidden = true;
    elements.emptyState.hidden = false;
    elements.emptyState.textContent = "Loading frame " + position + "…";
  }
  var preloadedImage = new Image();
  preloadedImage.alt = "Replay frame " + position;
  preloadedImage.onload = function () {
    if (requestGeneration !== imageRequestGeneration || lastImageKey !== imageKey) return;
    preloadedImage.onload = null;
    preloadedImage.onerror = null;
    elements.frameImage.replaceWith(preloadedImage);
    elements.frameImage = preloadedImage;
    loadedImageKey = imageKey;
    elements.viewerFrame.hidden = false;
    elements.emptyState.hidden = true;
  };
  preloadedImage.onerror = function () {
    if (requestGeneration !== imageRequestGeneration || lastImageKey !== imageKey) return;
    preloadedImage.onload = null;
    preloadedImage.onerror = null;
    loadedImageKey = "";
    elements.viewerFrame.hidden = true;
    elements.emptyState.hidden = false;
    elements.emptyState.textContent = "Frame image is unavailable.";
  };
  preloadedImage.src = imageUrl;
}
function queueSeek(position) {
  seekQueued = position;
  if (!scrubbing) flushSeek();
}
function flushSeek() {
  if (seekQueued == null || scrubbing || actionInFlight || controlInFlight) return;
  var position = seekQueued;
  seekQueued = null;
  seekTarget = position;
  seekInFlight = true;
  var id = ++seekRequestId;
  var request = beginStateRequest("seek");
  setNotice("Processing frames to " + position + "…", true);
  fetch("api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "seek", run_id: selectedRunId("seek"), position: position })
  }).then(function (response) {
    return response.json().then(function (payload) { return {ok: response.ok, payload: payload}; });
  }).then(function (result) {
    if (id !== seekRequestId) return;
    seekInFlight = false;
    seekTarget = null;
    if (!acceptStateResponse(request, result.payload.state)) return;
    if (result.payload.state) render(result.payload.state);
    if (!result.ok || result.payload.ok === false) {
      pendingResume = false;
      setNotice(result.payload.message || "Seek was rejected.");
    } else {
      setNotice("");
    }
  }).catch(function (error) {
    if (id !== seekRequestId) return;
    seekInFlight = false;
    seekTarget = null;
    pendingResume = false;
    if (stateRequestIsCurrent(request)) setNotice("Workbench connection failed: " + error);
  }).finally(function () {
    if (id !== seekRequestId) return;
    flushSeek();
    flushPluginSelection();
    flushResume();
  });
}
function flushResume() {
  if (!pendingResume || scrubbing || seekInFlight || seekQueued != null || actionInFlight || controlInFlight) return;
  pendingResume = false;
  if (state && state.phase === "paused") action("resume");
}
function beginScrub() {
  if (scrubbing) return;
  if (!state || (state.phase !== "running" && state.phase !== "paused")) return;
  scrubbing = true;
  resumeAfterScrub = state.phase === "running";
  pendingResume = false;
  clearRecordSelection();
  if (resumeAfterScrub) {
    action("pause").then(function () { flushSeek(); });
  }
}
function onSeekInput() {
  if (!state || (state.phase !== "running" && state.phase !== "paused" && !scrubbing)) return;
  if (!scrubbing) beginScrub();
  var position = Number(elements.seekBar.value);
  if (!Number.isFinite(position)) return;
  var lastIndex = Number(elements.seekBar.max);
  if (!Number.isFinite(lastIndex)) lastIndex = position;
  position = Math.max(0, Math.min(Math.round(position), lastIndex));
  scrubPosition = position;
  elements.seekBar.value = String(position);
  showScrubImage(position);
  queueSeek(position);
}
function endScrub() {
  if (!scrubbing) return;
  scrubbing = false;
  flushSeek();
  if (resumeAfterScrub) {
    pendingResume = true;
    flushResume();
  }
  resumeAfterScrub = false;
}
elements.seekBar.addEventListener("pointerdown", function () { beginScrub(); });
elements.seekBar.addEventListener("input", onSeekInput);
elements.seekBar.addEventListener("change", function () {
  if (!scrubbing) onSeekInput();
  endScrub();
});
elements.seekBar.addEventListener("keyup", function () {
  if (scrubbing) endScrub();
});
window.addEventListener("pointerup", function () {
  if (scrubbing) endScrub();
});
elements.loopToggle.addEventListener("click", function () {
  queueLoop(!loopEnabled());
});
