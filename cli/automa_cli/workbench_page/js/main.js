// Top-level render, failure banner, and polling; loads last and starts the poll.
// Classic script: the page's files share one global scope.
"use strict";

function renderFailure() {
  var failure = state && state.failure;
  elements.failurePanel.hidden = !failure;
  if (failure) {
    var recovery = state && state.recovery_action;
    elements.failureText.textContent = text(failure.boundary, "failure") + ": " + text(failure.message, "unknown failure") +
      (recovery ? "\nNext action: " + recovery : "");
  }
}
function render(nextState, options) {
  options = options || {};
  state = nextState || state;
  if (!state) return;
  var source = state.source || {};
  var identity = state.source_identity || "no source selected";
  elements.sourceIdentity.title = identity;
  setText("sourceIdentity", shortId(identity));
  if (document.activeElement !== elements.sourceDir) {
    var sourcePath = source.source_path || source.path;
    if (sourcePath) {
      elements.sourceDir.value = sourcePath;
      remember(SOURCE_DIR_KEY, sourcePath);
    }
  }
  var perception = currentPayload("perception");
  var displayedFrame = currentFrame();
  setText("perceptionStatus", displayedFrame && displayedFrame.absent
    ? "absent" : perception && perception.status);
  var progress = state.progress || {};
  setText("progressText", text(progress.completed, "0") + " / " + text(progress.total, "0"));
  var total = Number(progress.total) || 0;
  var completed = Number(progress.completed) || 0;
  var lastIndex = Math.max(total - 1, 0);
  elements.seekBar.max = String(lastIndex);
  elements.seekBar.disabled = total === 0 || !state ||
    (state.phase !== "running" && state.phase !== "paused" && !scrubbing);
  if (!scrubbing && document.activeElement !== elements.seekBar) {
    var frame = currentFrame();
    var position = frame && frame.position != null ? Number(frame.position) : Math.max(completed - 1, 0);
    elements.seekBar.value = String(Math.min(Math.max(position, 0), lastIndex));
  }
  var controls = state.controls || {};
  if (document.activeElement !== elements.cadence && (!cadenceDraft || state.phase !== "idle")) {
    elements.cadence.value = controls.pace === "realtime"
      ? "realtime" : text(controls.cadence_ms, "250");
  }
  if (loopDraft !== null && typeof controls.loop === "boolean" && controls.loop === loopDraft) {
    loopDraft = null;
  }
  if (loopDraft !== null) {
    paintLoopToggle(loopDraft);
  } else if (typeof controls.loop === "boolean") {
    paintLoopToggle(controls.loop);
  }
  if (!options.skipViewer) {
    if (scrubbing) {
      var live = state.current_frame;
      if (live && Number(live.position) === scrubPosition) {
        renderFrame();
      }
    } else {
      renderFrame();
    }
    var stepsKey = text(state.run_id, "") + ":" + text(state.steps_revision, "");
    if (stepsKey !== renderedStepsKey) {
      renderedStepsKey = stepsKey;
      renderPerception();
      renderMemory();
      renderDecision();
    }
    renderFailure();
  }
  renderControls();
}
async function poll() {
  if (pollInFlight || actionInFlight) {
    schedulePoll(pollDelay());
    return;
  }
  pollInFlight = true;
  var requestGeneration = stateRequestGeneration;
  try {
    var response = await fetch("/api/state", { cache: "no-store" });
    var payload = response.ok ? await response.json() : null;
    if (payload && requestGeneration === stateRequestGeneration) render(payload);
  } catch (error) {
    if (requestGeneration === stateRequestGeneration) {
      setNotice("Workbench connection failed: " + error);
    }
  } finally {
    pollInFlight = false;
    schedulePoll(pollDelay());
  }
}
function pollDelay() {
  return state && (state.phase === "running" || state.phase === "paused") ? 250 : 2000;
}
function schedulePoll(delay) {
  if (pollTimer !== null) window.clearTimeout(pollTimer);
  pollTimer = window.setTimeout(poll, delay);
}
poll();
