// Frame region: the viewer, floating frames, recall, and the overlay.
// Classic script: the page's files share one global scope.
"use strict";

var frameLayer = 30;
var floatingFrames = {};
var FRAME_PAD = 8;
function viewerRect() {
  return elements.viewerStage.getBoundingClientRect();
}
function frameAnchorRect(root, size) {
  var stage = viewerRect();
  var pad = 12;
  var width = size.width;
  var height = size.height;
  var left = stage.left + pad;
  var top = stage.top + pad;
  var maxX = Math.max(left, stage.right - width - pad);
  var maxY = Math.max(top, stage.bottom - height - pad);
  var anchor = root.getAttribute("data-frame-anchor") || "tl";
  if (anchor === "tr") return { x: maxX, y: top };
  if (anchor === "bl") return { x: left, y: maxY };
  if (anchor === "br") return { x: maxX, y: maxY };
  return { x: left, y: top };
}
function clampFrameRect(position, size, minSize) {
  var maxW = Math.max(minSize.width, window.innerWidth - FRAME_PAD * 2);
  var maxH = Math.max(minSize.height, window.innerHeight - FRAME_PAD * 2);
  var width = Math.max(minSize.width, Math.min(size.width, maxW));
  var height = Math.max(minSize.height, Math.min(size.height, maxH));
  return {
    position: {
      x: Math.min(Math.max(FRAME_PAD, position.x), Math.max(FRAME_PAD, window.innerWidth - width - FRAME_PAD)),
      y: Math.min(Math.max(FRAME_PAD, position.y), Math.max(FRAME_PAD, window.innerHeight - height - FRAME_PAD))
    },
    size: { width: width, height: height }
  };
}
function bindFloatingFrame(root) {
  var storageKey = "automa-workbench-vframe-" + root.id;
  var minSize = { width: 220, height: 140 };
  var defaultSize = {
    width: Number(root.getAttribute("data-frame-width")) || 260,
    height: Number(root.getAttribute("data-frame-height")) || 200
  };
  var stored = rememberedJson(storageKey);
  var state = {
    position: stored && stored.position ? stored.position : frameAnchorRect(root, defaultSize),
    size: stored && stored.size ? stored.size : defaultSize,
    minimized: !!(stored && stored.minimized),
    closed: !!(stored && stored.closed)
  };
  var header = root.querySelector("[data-frame-header]");
  var minimize = root.querySelector("[data-frame-minimize]");
  var closeButton = root.querySelector("[data-frame-close]");
  var drag = null;
  var resize = null;

  function persist() { rememberJson(storageKey, state); }

  function apply() {
    var rect = clampFrameRect(state.position, state.size, minSize);
    state.position = rect.position;
    state.size = rect.size;
    root.style.left = state.position.x + "px";
    root.style.top = state.position.y + "px";
    root.style.width = state.size.width + "px";
    root.style.height = state.minimized ? "auto" : state.size.height + "px";
    root.hidden = !!state.closed;
    root.setAttribute("data-closed", state.closed ? "true" : "false");
    root.setAttribute("data-minimized", state.minimized ? "true" : "false");
    if (minimize) {
      minimize.textContent = "";
      var mark = document.createElement("span");
      mark.style.display = "block";
      if (state.minimized) {
        mark.style.width = "10px";
        mark.style.height = "10px";
        mark.style.border = "1px solid currentColor";
      } else {
        mark.style.width = "12px";
        mark.style.borderTop = "1px solid currentColor";
      }
      minimize.appendChild(mark);
      minimize.setAttribute("aria-label", state.minimized ? "Expand" : "Minimize");
    }
    persist();
  }

  function raise() {
    frameLayer += 1;
    root.style.zIndex = String(frameLayer);
  }

  function onPointerMove(event) {
    if (drag) {
      state.position = {
        x: event.clientX - drag.offsetX,
        y: event.clientY - drag.offsetY
      };
      apply();
    }
    if (resize) {
      var dx = event.clientX - resize.startX;
      var dy = event.clientY - resize.startY;
      var next = { x: resize.x, y: resize.y, width: resize.width, height: resize.height };
      if (resize.dir.indexOf("e") >= 0) next.width = resize.width + dx;
      if (resize.dir.indexOf("s") >= 0) next.height = resize.height + dy;
      if (resize.dir.indexOf("w") >= 0) {
        next.width = resize.width - dx;
        next.x = resize.x + dx;
      }
      if (resize.dir.indexOf("n") >= 0) {
        next.height = resize.height - dy;
        next.y = resize.y + dy;
      }
      state.position = { x: next.x, y: next.y };
      state.size = { width: next.width, height: next.height };
      apply();
    }
  }

  function onPointerUp() {
    drag = null;
    resize = null;
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", onPointerUp);
    window.removeEventListener("pointercancel", onPointerUp);
  }

  function startPointerSession() {
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
  }

  ["n","s","e","w","ne","nw","se","sw"].forEach(function (dir) {
    var handle = document.createElement("button");
    handle.type = "button";
    handle.className = "float-resize";
    handle.setAttribute("data-dir", dir);
    handle.setAttribute("aria-label", "Resize");
    root.appendChild(handle);
    handle.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      event.stopPropagation();
      raise();
      resize = {
        dir: dir,
        startX: event.clientX,
        startY: event.clientY,
        x: state.position.x,
        y: state.position.y,
        width: state.size.width,
        height: state.size.height
      };
      startPointerSession();
    });
  });

  header.addEventListener("pointerdown", function (event) {
    if (event.target.closest("[data-frame-minimize], [data-frame-close], button, a, input, select")) return;
    event.preventDefault();
    raise();
    drag = {
      offsetX: event.clientX - state.position.x,
      offsetY: event.clientY - state.position.y
    };
    startPointerSession();
  });
  root.addEventListener("pointerdown", raise, true);

  if (minimize) {
    minimize.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      state.minimized = !state.minimized;
      apply();
      syncRecall();
    });
  }

  if (closeButton) {
    closeButton.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      state.closed = true;
      apply();
      syncRecall();
    });
  }

  function restore() {
    state.closed = false;
    state.minimized = false;
    raise();
    apply();
    syncRecall();
  }

  apply();
  floatingFrames[root.id] = {
    restore: restore,
    isMinimized: function () { return state.minimized; },
    isClosed: function () { return state.closed; }
  };
  window.addEventListener("resize", apply);
  if (!stored) {
    window.requestAnimationFrame(function () {
      state.position = frameAnchorRect(root, state.size);
      apply();
    });
  }
}
function syncRecall() {
  Array.prototype.forEach.call(document.querySelectorAll("[data-recall]"), function (button) {
    var frame = floatingFrames[button.getAttribute("data-recall")];
    var open = frame && !frame.isClosed() && !frame.isMinimized();
    button.setAttribute("aria-pressed", open ? "true" : "false");
  });
}
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
["memoryFrame", "perceptionFrame", "decisionFrame"].forEach(function (id) {
  bindFloatingFrame(document.getElementById(id));
});
Array.prototype.forEach.call(document.querySelectorAll("[data-recall]"), function (button) {
  button.addEventListener("click", function () {
    var frame = floatingFrames[button.getAttribute("data-recall")];
    if (frame) frame.restore();
  });
});
syncRecall();
elements.overlayToggle.addEventListener("click", function () {
  var next = elements.overlayToggle.value === "off" ? "on" : "off";
  elements.overlayToggle.value = next;
  elements.overlayToggle.textContent = next === "on" ? "overlays" : "image";
  elements.overlayToggle.setAttribute("aria-pressed", next === "on" ? "true" : "false");
  drawOverlay();
});
window.addEventListener("resize", drawOverlay);
