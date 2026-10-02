// Plugin panels: the perception and memory catalogs and their selection drafts.
// Classic script: the page's files share one global scope.
"use strict";

// Each panel keeps its own draft, and the newest selection toggled while another request
// was in flight (queued; sent when it settles).
function newPluginPanel(catalogId, summaryId, digestId, catalogKey, activeKey) {
  return {
    catalogId: catalogId, summaryId: summaryId, digestId: digestId,
    catalogKey: catalogKey, activeKey: activeKey,
    draft: null, draftDigest: null, queued: null, renderKey: null
  };
}
var pluginPanels = {
  perception: newPluginPanel(
    "pluginCatalog", "pluginSelectionSummary", "pluginDigest",
    "plugin_catalog", "active_plugin_ids"
  ),
  memory: newPluginPanel(
    "memoryPluginCatalog", "memoryPluginSelectionSummary", "memoryPluginDigest",
    "memory_plugin_catalog", "active_memory_plugin_ids"
  )
};
function queuePluginSelection(step, ids) {
  pluginPanels[step].queued = ids;
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
  return Array.prototype.map.call(
    elements[panel.catalogId].querySelectorAll("input[data-plugin-id]:checked"),
    function (input) { return input.getAttribute("data-plugin-id"); }
  ).filter(function (value) { return value; });
}
function renderPluginSummary(panel, catalog, plugins, active) {
  setText(panel.summaryId, active.length + " active · " + plugins.length + " available");
  setText(panel.digestId, catalog ? "catalog " + text(catalog.digest) : "");
}
function renderPluginPanel(step) {
  var panel = pluginPanels[step];
  var container = elements[panel.catalogId];
  var catalog = state && state[panel.catalogKey];
  var plugins = catalog && Array.isArray(catalog.plugins) ? catalog.plugins : [];
  var stateActive = state && Array.isArray(state[panel.activeKey]) ? state[panel.activeKey] : [];
  if (!catalog || panel.draftDigest !== catalog.digest) {
    panel.draft = stateActive.slice();
    panel.draftDigest = catalog ? catalog.digest : null;
  }
  var active = panel.draft || stateActive;
  var allowed = state && state.controls && state.controls.allowed_actions
    ? state.controls.allowed_actions : [];
  var selectionAllowed = allowed.indexOf("select_plugins") >= 0;
  var renderKey = catalog ? "catalog:" + text(catalog.digest, "") : "empty";
  // Keep checkbox nodes through state polls so an in-progress click is not detached.
  if (renderKey === panel.renderKey) {
    Array.prototype.forEach.call(
      container.querySelectorAll("input[data-plugin-id]"),
      function (input) {
        input.disabled = !selectionAllowed;
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
    empty.textContent = "No packaged plugins found.";
    container.appendChild(empty);
  }
  plugins.forEach(function (plugin) {
    var item = document.createElement("label");
    item.className = "plugin-entry";
    var checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.setAttribute("data-plugin-id", text(plugin.id, ""));
    checkbox.checked = active.indexOf(plugin.id) >= 0;
    checkbox.disabled = !selectionAllowed;
    checkbox.addEventListener("change", function () {
      panel.draft = selectedPluginIdsFromView(panel);
      panel.draftDigest = catalog ? catalog.digest : null;
      renderPluginSummary(panel, catalog, plugins, panel.draft);
      queuePluginSelection(step, panel.draft);
    });
    item.appendChild(checkbox);
    var copy = document.createElement("span");
    var title = document.createElement("strong");
    title.textContent = text(plugin.name, plugin.id);
    copy.appendChild(title);
    if (plugin.description) title.title = plugin.description;
    item.appendChild(copy);
    container.appendChild(item);
  });
  renderPluginSummary(panel, catalog, plugins, active);
}
function renderPlugins() {
  Object.keys(pluginPanels).forEach(renderPluginPanel);
}
