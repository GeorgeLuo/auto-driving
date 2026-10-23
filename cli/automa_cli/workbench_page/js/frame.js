// Frame region: the viewer and its overlay.
// Classic script: the page's files share one global scope.
"use strict";

function renderFrame() {
  var frame = currentFrame();
  if (!frame) {
    setText("frameId", null);
    setText("frameIndex", null);
    setText("frameTimestamp", null);
    elements.overlayCanvas.width = 1;
    elements.overlayCanvas.height = 1;
    if (loadedImageKey !== "") {
      elements.viewerFrame.hidden = false;
      elements.emptyState.hidden = true;
      return;
    }
    lastImageKey = "";
    elements.frameImage.removeAttribute("src");
    elements.viewerFrame.hidden = true;
    elements.emptyState.hidden = false;
    elements.emptyState.textContent = "No frame selected";
    return;
  }
  setText("frameId", frame.frame_id);
  setText("frameIndex", frame.position);
  setText("frameTimestamp", frame.timestamp_ms);
  elements.frameImage.alt = "Replay frame " + text(frame.frame_id, "unknown");
  if (frame.absent) {
    lastImageKey = "";
    loadedImageKey = "";
    elements.frameImage.removeAttribute("src");
    elements.overlayCanvas.width = 1;
    elements.overlayCanvas.height = 1;
    elements.viewerFrame.hidden = true;
    elements.emptyState.hidden = false;
    elements.emptyState.textContent = "Absent frame · " + text(frame.absence_reason, "reason not provided");
    return;
  }
  var imageKey = text(state.run_id, "") + ":" + text(frame.frame_id, "");
  if (imageKey !== lastImageKey) {
    lastImageKey = imageKey;
    var hasRenderedImage = loadedImageKey !== "";
    var requestGeneration = ++imageRequestGeneration;
    var imageUrl = "/api/frame?run_id=" + encodeURIComponent(state.run_id || "") +
      "&frame_id=" + encodeURIComponent(frame.frame_id);
    elements.overlayCanvas.width = 1;
    elements.overlayCanvas.height = 1;
    if (!hasRenderedImage) {
      elements.viewerFrame.hidden = true;
      elements.emptyState.hidden = false;
      elements.emptyState.textContent = "Loading frame " + text(frame.frame_id, "unknown") + "…";
    } else {
      elements.viewerFrame.hidden = false;
      elements.emptyState.hidden = true;
    }
    var preloadedImage = new Image();
    preloadedImage.alt = elements.frameImage.alt;
    preloadedImage.onload = function () {
      if (requestGeneration !== imageRequestGeneration || lastImageKey !== imageKey) return;
      preloadedImage.onload = null;
      preloadedImage.onerror = null;
      elements.frameImage.replaceWith(preloadedImage);
      elements.frameImage = preloadedImage;
      loadedImageKey = imageKey;
      elements.viewerFrame.hidden = false;
      elements.emptyState.hidden = true;
      drawOverlay();
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
    return;
  }
  if (loadedImageKey !== imageKey) return;
  elements.viewerFrame.hidden = false;
  elements.emptyState.hidden = true;
  drawOverlay();
}
function drawOverlay() {
  var frame = currentFrame();
  var image = elements.frameImage;
  var canvas = elements.overlayCanvas;
  var imageKey = frame
    ? text(state && state.run_id, "") + ":" + text(frame.frame_id, "")
    : "";
  if (!frame || frame.absent || loadedImageKey !== imageKey || !image.naturalWidth || elements.overlayToggle.value === "off") {
    canvas.width = 1; canvas.height = 1;
    return;
  }
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  var context = canvas.getContext("2d");
  context.clearRect(0, 0, canvas.width, canvas.height);
  var observation = currentPayload("observation");
  var things = observation && Array.isArray(observation.things) ? observation.things : [];
  var labels = [];
  context.strokeStyle = "rgba(57, 255, 20, .95)";
  context.fillStyle = "rgba(57, 255, 20, .12)";
  context.lineWidth = Math.max(2, canvas.width / 500);
  things.forEach(function (thing) {
    var location = thing.location || {};
    if (thing.kind === "sensor_frame" || location.frame !== "image") return;
    var box = location.bbox_xyxy_norm;
    var labelX = 4;
    var labelY = 14;
    if (Array.isArray(box) && box.length === 4) {
      var x = box[0] * canvas.width;
      var y = box[1] * canvas.height;
      var width = (box[2] - box[0]) * canvas.width;
      var height = (box[3] - box[1]) * canvas.height;
      context.fillRect(x, y, width, height);
      context.strokeRect(x, y, width, height);
      labelX = x + 4;
      labelY = Math.max(14, y - 4);
    }
    var polygon = location.polygon_xy_norm;
    if (Array.isArray(polygon) && polygon.length > 2) {
      context.beginPath();
      polygon.forEach(function (point, index) {
        var px = point[0] * canvas.width;
        var py = point[1] * canvas.height;
        if (index === 0) context.moveTo(px, py); else context.lineTo(px, py);
      });
      context.closePath();
      context.fill();
      context.stroke();
    }
    labels.push({
      text: text(thing.label, thing.kind),
      x: labelX,
      y: labelY
    });
  });
  context.font = Math.max(11, canvas.width / 60) + "px \"JetBrains Mono\", monospace";
  var placed = [];
  labels.forEach(function (label) {
    var width = context.measureText(label.text).width + 6;
    var x = Math.min(Math.max(2, label.x), Math.max(2, canvas.width - width - 2));
    var y = Math.min(Math.max(12, label.y), canvas.height - 4);
    var attempt = 0;
    while (attempt < 12) {
      var clash = placed.some(function (prior) {
        return Math.abs(y - prior.y) < 15 && x < prior.x + prior.w && x + width > prior.x;
      });
      if (!clash) break;
      y = Math.min(y + 15, canvas.height - 4);
      attempt += 1;
    }
    placed.push({ x: x, y: y, w: width });
    context.fillStyle = "rgba(10, 10, 10, .78)";
    context.fillRect(x - 2, y - 11, width, 14);
    context.fillStyle = "#ededed";
    context.fillText(label.text, x, y);
  });
}
elements.overlayToggle.addEventListener("click", function () {
  var next = elements.overlayToggle.value === "off" ? "on" : "off";
  elements.overlayToggle.value = next;
  elements.overlayToggle.setAttribute("aria-pressed", next === "on" ? "true" : "false");
  drawOverlay();
});
window.addEventListener("resize", drawOverlay);
