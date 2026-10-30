// The status line and metric text shared by the runtime viewer's perception,
// memory, and decision pages.
// Classic script: the page's files share one global scope.
"use strict";

function setStatus(text, mode) {
  document.getElementById("statusText").textContent = text;
  document.getElementById("statusDot").className = `status-dot ${mode || ""}`;
}

function formatMs(value) {
  const number = Number(value);
  if (value == null || !Number.isFinite(number)) return "—";
  return `${Math.round(number * 1000) / 1000} ms`;
}

function confidenceText(value, digits = 3) {
  if (value === null || value === undefined) return "none";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "none";
}

function appendMetric(container, name, value) {
  const term = document.createElement("dt");
  term.textContent = name;
  const detail = document.createElement("dd");
  detail.textContent = value ?? "unknown";
  container.append(term, detail);
}
