// Shared page state, element table, storage helpers, and frame selection accessors.
// Classic script: the page's files share one global scope.
"use strict";

var state = null;
var renderedStepsKey = "";
var lastImageKey = "";
var loadedImageKey = "";
var pollInFlight = false;
var pollTimer = null;
var actionInFlight = false;
var stateRequestGeneration = 0;
var imageRequestGeneration = 0;
var elements = {};
[
  "sourceIdentity", "emptyState", "viewerFrame", "frameImage",
  "overlayCanvas", "viewerStage", "frameId", "frameIndex", "frameTimestamp", "perceptionStatus",
  "progressText", "seekBar", "evidenceSummary", "pluginRuns",
  "evidenceCounts", "evidenceText", "sourceDir", "cadence", "loopToggle", "overlayToggle",
  "startButton", "pauseButton", "resumeButton", "stepButton",
  "resetButton", "notice",
  "pluginSelectionSummary", "pluginCatalog", "pluginDigest", "memorySelection", "memoryHealth", "memoryEpoch",
  "memoryCount", "memoryPolicy", "memoryDrops", "memoryDropsSummary", "memoryDropsText",
  "memorySearch", "memoryRecords", "failurePanel", "failureText",
  "memorySelected", "decisionSummary", "decisionFrameIdentity", "decisionStatus", "decisionSelected",
  "decisionProposed", "decisionAuthority", "decisionReason", "decisionSource", "decisionCandidates"
].forEach(function (id) { elements[id] = document.getElementById(id); });
var noticeTimer = null;
var SOURCE_DIR_KEY = "automa-workbench-source-dir";
function setNotice(message) {
  if (noticeTimer !== null) {
    window.clearTimeout(noticeTimer);
    noticeTimer = null;
  }
  elements.notice.classList.remove("is-fading");
  elements.notice.textContent = message || "";
  if (!message) return;
  void elements.notice.offsetWidth;
  noticeTimer = window.setTimeout(function () {
    elements.notice.classList.add("is-fading");
    noticeTimer = window.setTimeout(function () {
      elements.notice.classList.remove("is-fading");
      elements.notice.textContent = "";
      noticeTimer = null;
    }, 250);
  }, 1000);
}
function remembered(key) {
  try { return window.localStorage.getItem(key) || ""; }
  catch (error) { return ""; }
}
function remember(key, value) {
  try {
    var trimmed = String(value || "").trim();
    if (trimmed) window.localStorage.setItem(key, trimmed);
    else window.localStorage.removeItem(key);
  } catch (error) {}
}
function rememberJson(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); }
  catch (error) {}
}
function rememberedJson(key) {
  try {
    var raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (error) { return null; }
}
function text(value, fallback) {
  if (value === null || value === undefined || value === "") return fallback || "—";
  return String(value);
}
function shortId(value) {
  var raw = text(value, "");
  if (raw.length <= 28) return raw;
  return raw.slice(0, 8) + "…" + raw.slice(-12);
}
function setText(id, value, fallback) {
  elements[id].textContent = text(value, fallback);
}
function currentFrame() {
  return state && state.current_frame ? state.current_frame : null;
}
function currentPayload(key) {
  return state && state.steps ? state.steps[key] : null;
}
function clearRecordSelection() {
  selectedRecordId = null;
  renderedStepsKey = "";
}
function selectedRunId(action) {
  if (!state || action === "start") return null;
  return state.run_id || null;
}
function setButton(id, allowed) {
  elements[id].disabled = !allowed;
}
elements.sourceDir.value = remembered(SOURCE_DIR_KEY);
elements.sourceDir.addEventListener("change", function () {
  remember(SOURCE_DIR_KEY, elements.sourceDir.value);
});
