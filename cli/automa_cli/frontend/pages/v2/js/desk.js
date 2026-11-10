// Window plumbing every window shares. A window's view returns what it shows, and
// paintWindow writes that into slots made once and refilled, so nothing churns as state
// changes. A view returns:
//   mode     the title bar's text after the window's name
//   verbs    the list's top line: [{label, action, disabled, gap}]
//   rows     the list: [{name, action, disabled, selected, marked, exited, child}]
//   form     the data-form shown at the pane's top, or none
//   status   the pane's text, scrolled to its end as it changes (a command's output); keepScroll
//            holds its scroll instead, fromTop shows its top (a listing); statusGap marks it
//   keys     the pane's bottom-right keys, like verbs
//   feed     true when the pane shows a frame instead of text (Stream)
"use strict";

const $ = (id) => document.getElementById(id);
const titleCase = (word) => word[0].toUpperCase() + word.slice(1);
const setText = (el, text) => { if (el.textContent !== text) el.textContent = text; };
// Keys that take something away (a record, a listing, an arming, a form) carry a red tint.
const REMOVING = new Set(["DEL", "UNTRK", "CANCEL", "DSARM", "DSALL"]);

// Every window's title bar comes from one template.
function addChrome(root) {
  const header = $("frameChrome").content.firstElementChild.cloneNode(true);
  header.querySelector("[data-title]").textContent = root.dataset.title;
  header.querySelector("[data-frame-close]").setAttribute("aria-label", `Close ${root.dataset.title}`);
  root.prepend(header);
}
document.querySelectorAll(".float-frame[data-title]").forEach(addChrome);

// Opens a window, or brings it forward when it is open.
const bringUp = (frameId) => floatingFrames[frameId].restore();

// A run of slots in one container, grown as items need them and hidden when unused.
// A click runs the action the last fill gave its slot.
function slotList(container, make) {
  const list = { container, make, slots: [], actions: [] };
  container.addEventListener("click", (event) => {
    const slot = event.target.closest("button");
    const action = slot && !slot.disabled && list.actions[list.slots.indexOf(slot)];
    if (action) action();
  });
  return list;
}
function fillSlots(list, items, paintSlot) {
  while (list.slots.length < items.length) list.slots.push(list.container.appendChild(list.make()));
  list.slots.forEach((slot, i) => {
    const item = items[i];
    slot.hidden = !item;
    list.actions[i] = item && !item.disabled ? item.action || null : null;
    if (item) paintSlot(slot, item);
  });
}
function makeButton(className) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  return button;
}
function paintKey(button, key) {
  setText(button, key.label);
  button.dataset.tone = REMOVING.has(key.label) ? "remove" : "";
  button.disabled = !!key.disabled || !key.action;
  markGap(button, key.gap);
}
function paintRow(button, row) {
  setText(button, row.name);
  const className = ["row", row.selected && "selected", row.marked && "marked", row.exited && "exited", row.child && "child"]
    .filter(Boolean).join(" ");
  if (button.className !== className) button.className = className;
  button.disabled = !!row.disabled || !row.action;
}

function deskWindow(root, view) {
  const part = (selector, make) => { const el = root.querySelector(selector); return el && slotList(el, make); };
  const w = {
    root, view,
    modeEl: root.querySelector("[data-mode]"),
    text: root.querySelector(".float-frame-status-text"),
    forms: [...root.querySelectorAll("[data-form]")],
    verbs: part("[data-verbs]", () => makeButton("key")),
    rows: part("[data-rows]", () => makeButton("row")),
    keys: part("[data-keys]", () => makeButton("key")),
  };
  // Forms answer as they are filled: a key enables once its flags are given.
  root.querySelectorAll(".pane-form :is(input, select)").forEach((input) => {
    input.addEventListener("input", () => render());
    input.addEventListener("change", () => render());
  });
  return w;
}
function paintWindow(w) {
  const v = w.view();
  if (w.modeEl) setText(w.modeEl, v.mode || "");
  for (const form of w.forms) form.hidden = form.dataset.form !== v.form;
  if (w.verbs) fillSlots(w.verbs, v.verbs || [], paintKey);
  if (w.rows) fillSlots(w.rows, v.rows || [], paintRow);
  fillSlots(w.keys, v.keys || [], paintKey);
  if ("statusGap" in v) markGap(w.text.parentElement, v.statusGap);
  // An open form fills the pane's top; an error it answers prints under it.
  const open = w.forms.find((form) => !form.hidden);
  w.text.hidden = !!v.feed || (!!open && !v.status);
  w.text.style.top = open ? `${open.offsetTop + open.offsetHeight}px` : "";
  if (w.text.textContent !== v.status) {
    const top = w.text.scrollTop;
    w.text.textContent = v.status;
    w.text.scrollTop = v.keepScroll ? top : v.fromTop ? 0 : w.text.scrollHeight;
  }
  return v;
}

// Touch a select's options only when they change: rewriting them on every paint
// keeps re-opening a native dropdown, and one in use is left alone.
function fillSelect(select, wanted) {
  const key = JSON.stringify(wanted);
  if (select.dataset.filled === key || document.activeElement === select) return;
  select.dataset.filled = key;
  while (select.options.length < wanted.length) select.add(new Option());
  [...select.options].forEach((option, i) => {
    const hidden = i >= wanted.length;
    if (option.hidden !== hidden) option.hidden = hidden;
    const disabled = hidden || !wanted[i][0];
    if (option.disabled !== disabled) option.disabled = disabled;
    if (!hidden && option.value !== wanted[i][0]) option.value = wanted[i][0];
    if (!hidden && option.textContent !== wanted[i][1]) option.textContent = wanted[i][1];
  });
  if (select.selectedIndex < 0 || select.selectedIndex >= wanted.length) select.selectedIndex = 0;
}

// ---- A window's commands and reads. W is the window's state: busy, reading, ask, log. ----
// What a command printed: its text, or the route's refusal.
const printed = (result) => {
  const text = result.text ?? result.error ?? "";
  return text ? text.split("\n") : [];
};
// A write: the pane shows "..." until the command prints, its lines as they arrive (where the
// route streams them), then all it printed. Every read is taken again before its keys return.
async function command(W, route, body) {
  W.busy = true; W.ask = null; W.log = ["..."]; render();
  const result = await api.write(route, body, {
    onLine(line) {
      if (W.log.length === 1 && W.log[0] === "...") W.log = [];
      W.log.push(line); render();
    },
  });
  W.log = printed(result);
  await refresh();
  W.busy = false; render();
  return result;
}
// A selection's read: the pane shows "..." until it answers.
async function readFor(W, route) {
  const n = (W.reads = (W.reads || 0) + 1);
  W.reading = true; W.log = []; W.ask = null; render();
  await api.read(route);
  if (n === W.reads) { W.reading = false; render(); }
}

// Mock only: mock.js numbers what the CLI cannot back yet; without it this does nothing.
function markGap(el, gap) {
  if (typeof mock !== "undefined") mock.markGap(el, gap);
}
