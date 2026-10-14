// Plugin panels: the perception, memory, proposal and plan catalogs and their selection drafts.
// Classic script: the page's files share one global scope.
"use strict";

// Each panel keeps its own draft, and the newest selection toggled while another request
// was in flight (queued; sent when it settles).
function newPluginPanel(step) {
  return {
    step: step, catalogId: step + "PluginCatalog",
    summaryId: step + "PluginSelectionSummary", digestId: step + "PluginDigest",
    orderId: step + "PluginOrder", presetKey: step + "_preset",
    catalogKey: step + "_plugin_catalog", activeKey: "active_" + step + "_plugin_ids",
    draft: null, draftDigest: null, queued: null, renderKey: null
  };
}
var pluginPanels = {};
function pluginPanel(step) {
  if (!pluginPanels[step]) pluginPanels[step] = newPluginPanel(step);
  return pluginPanels[step];
}
// The selection queue addresses these panels before a catalog is loaded.
pluginPanel("perception");
pluginPanel("memory");
function catalogSteps() {
  if (!state) return [];
  return Object.keys(state).filter(function (key) {
    return key.endsWith("_plugin_catalog");
  }).map(function (key) {
    return key.slice(0, -"_plugin_catalog".length);
  });
}
function mountPluginPanel(panel) {
  // A test proxy creates a node on get. The page has no node until this mounts one.
  if (elements[panel.catalogId]) return;
  var root = document.getElementById("pluginSections");
  if (!root) return;
  var section = document.createElement("section");
  section.className = "plugin-section";
  var kicker = document.createElement("span");
  kicker.className = "kicker";
  kicker.textContent = panel.step;
  var summary = document.createElement("span");
  summary.id = panel.summaryId;
  summary.className = "plugin-selection-summary muted";
  summary.textContent = "no catalog";
  var order = document.createElement("span");
  order.id = panel.orderId;
  order.className = "plugin-order muted help";
  var catalogNode = document.createElement("div");
  catalogNode.id = panel.catalogId;
  catalogNode.className = "plugin-catalog";
  var digest = document.createElement("div");
  digest.id = panel.digestId;
  digest.className = "digest muted";
  section.appendChild(kicker);
  section.appendChild(summary);
  section.appendChild(order);
  section.appendChild(catalogNode);
  section.appendChild(digest);
  root.appendChild(section);
  elements[panel.summaryId] = summary;
  elements[panel.orderId] = order;
  elements[panel.catalogId] = catalogNode;
  elements[panel.digestId] = digest;
}
function ensurePluginPanels() {
  catalogSteps().forEach(function (step) {
    mountPluginPanel(pluginPanel(step));
  });
}
function queuePluginSelection(step, ids) {
  pluginPanel(step).queued = ids;
  flushPluginSelection();
}
function flushPluginSelection() {
  if (actionInFlight) return;
  Object.keys(pluginPanels).some(function (step) {
    var panel = pluginPanels[step];
    if (panel.queued === null) return false;
    var ids = panel.queued;
    panel.queued = null;
    action("select_plugins", { step: step, active_plugin_ids: ids });
    return true;
  });
}
// Once the server has answered, its selection replaces the draft unless a newer toggle is waiting.
// Without a step, every panel settles.
function settlePluginDraft(step) {
  (step ? [step] : Object.keys(pluginPanels)).forEach(function (name) {
    var panel = pluginPanels[name];
    if (!panel || panel.queued !== null) return;
    panel.draft = null;
    panel.draftDigest = null;
    panel.renderKey = null;
  });
}
// A rejected or failed selection drops anything waiting and shows the server's selection.
function revertPluginDraft(step) {
  (step ? [step] : Object.keys(pluginPanels)).forEach(function (name) {
    if (pluginPanels[name]) pluginPanels[name].queued = null;
  });
  settlePluginDraft(step);
}
function selectedPluginIdsFromView(panel) {
  var checked = Array.prototype.map.call(
    elements[panel.catalogId].querySelectorAll("input[data-plugin-id]:checked"),
    function (input) { return input.getAttribute("data-plugin-id"); }
  ).filter(function (value) { return value; });
  // Catalog display order is not execution order. Retain the draft or CLI
  // selection's order, removing unchecked ids and appending newly checked ids.
  var previous = panel.draft || (state && state[panel.activeKey]) || [];
  return previous.filter(function (id) { return checked.indexOf(id) >= 0; }).concat(
    checked.filter(function (id) { return previous.indexOf(id) < 0; })
  );
}
function renderPluginSummary(panel, catalog, plugins, active) {
  var pipeline = state && state.machine_detail && state.machine_detail.pipeline;
  var preset = panel.draft !== null ? "selection pending" : pipeline && pipeline[panel.presetKey];
  var counts = active.length + " active · " + plugins.length + " available";
  setText(panel.summaryId, preset ? preset + " · " + counts : counts);
  setText(panel.orderId, active.length ? "Run order: " + active.join(" → ")
    : "No " + panel.step + " plugins selected.");
  setText(panel.digestId, catalog ? "catalog " + text(catalog.digest) : "");
}
function renderPluginPanel(step) {
  var panel = pluginPanels[step];
  var container = elements[panel.catalogId];
  var catalog = state && state[panel.catalogKey];
  var plugins = catalog && Array.isArray(catalog.plugins) ? catalog.plugins : [];
  var stateActive = state && Array.isArray(state[panel.activeKey]) ? state[panel.activeKey] : [];
  if (!catalog || (panel.draft !== null && panel.draftDigest !== catalog.digest)) {
    panel.draft = null;
    panel.draftDigest = null;
  }
  var active = panel.draft || stateActive;
  var allowed = state && state.controls && state.controls.allowed_actions
    ? state.controls.allowed_actions : [];
  var selectionAllowed = allowed.indexOf("select_plugins") >= 0;
  var renderKey = catalog ? "catalog:" + text(catalog.digest, "") : "empty";
  // Keep the choice nodes through state polls so an in-progress click is not detached.
  if (renderKey === panel.renderKey) {
    Array.prototype.forEach.call(
      container.querySelectorAll("input[data-plugin-id]"),
      function (input) {
        input.disabled = !selectionAllowed;
        input.checked = active.indexOf(input.getAttribute("data-plugin-id")) >= 0;
      }
    );
    renderPluginSummary(panel, catalog, plugins, active);
    return;
  }
  panel.renderKey = renderKey;
  container.textContent = "";
  if (!plugins.length) {
    var empty = document.createElement("p");
    empty.className = "muted help";
    empty.textContent = "No plugins available.";
    container.appendChild(empty);
  }
  var single = !!(catalog && catalog.single_plugin);
  plugins.forEach(function (plugin) {
    var item = document.createElement("label");
    item.className = "plugin-entry";
    var choice = document.createElement("input");
    choice.type = single ? "radio" : "checkbox";
    if (single) choice.name = step + "Plugin";
    choice.setAttribute("data-plugin-id", text(plugin.id, ""));
    choice.checked = active.indexOf(plugin.id) >= 0;
    choice.disabled = !selectionAllowed;
    choice.addEventListener("change", function () {
      var ids = selectedPluginIdsFromView(panel);
      if (single && ids.length !== 1) {
        var restore = panel.draft && panel.draft.length === 1
          ? panel.draft
          : (state && state[panel.activeKey]) || [];
        Array.prototype.forEach.call(
          container.querySelectorAll("input[data-plugin-id]"),
          function (input) {
            input.checked = restore.indexOf(input.getAttribute("data-plugin-id")) >= 0;
          }
        );
        return;
      }
      panel.draft = ids;
      panel.draftDigest = catalog ? catalog.digest : null;
      renderPluginSummary(panel, catalog, plugins, panel.draft);
      queuePluginSelection(step, panel.draft);
    });
    item.appendChild(choice);
    var copy = document.createElement("span");
    var title = document.createElement("strong");
    title.textContent = text(plugin.name, plugin.id) + (plugin.revision ? " · " + plugin.revision.slice(0, 12) : "");
    copy.appendChild(title);
    if (plugin.description) title.title = plugin.description;
    item.appendChild(copy);
    container.appendChild(item);
  });
  renderPluginSummary(panel, catalog, plugins, active);
}
function renderPlugins() {
  ensurePluginPanels();
  catalogSteps().forEach(renderPluginPanel);
}
