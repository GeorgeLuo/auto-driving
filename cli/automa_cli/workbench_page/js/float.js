// Floating frames shared by the workbench and the runtime viewer.
// Classic script: the page's files share one global scope.
"use strict";

var frameLayer = 30;
var floatingFrames = {};
var FRAME_PAD = 8;

function rememberedJson(key) {
  try {
    var raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (error) { return null; }
}
function rememberJson(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); }
  catch (error) {}
}

function viewerRect() {
  if (typeof elements !== "undefined" && elements.viewerStage) {
    return elements.viewerStage.getBoundingClientRect();
  }
  return {
    left: 0,
    top: 0,
    right: window.innerWidth,
    bottom: window.innerHeight
  };
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
  if (anchor === "center") {
    return {
      x: Math.max(pad, ((stage.left + stage.right) - width) / 2),
      y: Math.max(pad, ((stage.top + stage.bottom) - height) / 2)
    };
  }
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
  var startClosed = root.getAttribute("data-frame-start") === "closed";
  var state = {
    position: stored && stored.position ? stored.position : frameAnchorRect(root, defaultSize),
    size: stored && stored.size ? stored.size : defaultSize,
    minimized: !!(stored && stored.minimized),
    closed: stored ? !!(stored.closed) : startClosed
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

  function noteRecall() {
    syncRecall();
  }

  ["n", "s", "e", "w", "ne", "nw", "se", "sw"].forEach(function (dir) {
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
      noteRecall();
    });
  }

  function close() {
    state.closed = true;
    apply();
    noteRecall();
  }

  if (closeButton) {
    closeButton.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      close();
    });
  }

  function restore() {
    state.closed = false;
    state.minimized = false;
    raise();
    apply();
    noteRecall();
  }

  apply();
  floatingFrames[root.id] = {
    restore: restore,
    close: close,
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

Array.prototype.forEach.call(document.querySelectorAll(".float-frame"), function (root) {
  bindFloatingFrame(root);
});

Array.prototype.forEach.call(document.querySelectorAll("[data-recall]"), function (button) {
  button.addEventListener("click", function () {
    var frame = floatingFrames[button.getAttribute("data-recall")];
    if (!frame) return;
    if (frame.isClosed() || frame.isMinimized()) frame.restore();
    else frame.close();
  });
});

syncRecall();
