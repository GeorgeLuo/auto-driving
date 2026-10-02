// Plugin panel: the perception catalog and the selection draft.
// Classic script: the page's files share one global scope.
"use strict";

var pluginSelectionDraft = null;
var pluginSelectionDraftDigest = null;
// The newest selection toggled while another request was in flight; sent when it settles.
var pluginSelectionQueued = null;
function queuePluginSelection(ids) {
  pluginSelectionQueued = ids;
  flushPluginSelection();
}
function flushPluginSelection() {
  if (pluginSelectionQueued === null || actionInFlight) return;
  var ids = pluginSelectionQueued;
  pluginSelectionQueued = null;
  action("select_plugins", { active_plugin_ids: ids });
}
// Once the server has answered, its selection replaces the draft unless a newer toggle is waiting.
function settlePluginDraft() {
  if (pluginSelectionQueued !== null) return;
  pluginSelectionDraft = null;
  pluginSelectionDraftDigest = null;
  pluginCatalogRenderKey = null;
}
// A rejected or failed selection drops anything waiting and shows the server's selection.
function revertPluginDraft() {
  pluginSelectionQueued = null;
  settlePluginDraft();
}
var pluginCatalogRenderKey = null;
function selectedPluginIdsFromView() {
  return Array.prototype.map.call(
    elements.pluginCatalog.querySelectorAll("input[data-plugin-id]:checked"),
    function (input) { return input.getAttribute("data-plugin-id"); }
  ).filter(function (value) { return value; });
}
function renderPluginSummary(catalog, plugins, active) {
  setText("pluginSelectionSummary", active.length + " active · " + plugins.length + " available");
  setText("pluginDigest", catalog ? "catalog " + text(catalog.digest) : "");
}
function renderPlugins() {
  var catalog = state && state.plugin_catalog;
  var plugins = catalog && Array.isArray(catalog.plugins) ? catalog.plugins : [];
  var stateActive = state && Array.isArray(state.active_plugin_ids)
    ? state.active_plugin_ids : [];
  if (!catalog || pluginSelectionDraftDigest !== catalog.digest) {
    pluginSelectionDraft = stateActive.slice();
    pluginSelectionDraftDigest = catalog ? catalog.digest : null;
  }
  var active = pluginSelectionDraft || stateActive;
  var allowed = state && state.controls && state.controls.allowed_actions
    ? state.controls.allowed_actions : [];
  var selectionAllowed = allowed.indexOf("select_plugins") >= 0;
  var renderKey = catalog ? "catalog:" + text(catalog.digest, "") : "empty";
  // Keep checkbox nodes through state polls so an in-progress click is not detached.
  if (renderKey === pluginCatalogRenderKey) {
    Array.prototype.forEach.call(
      elements.pluginCatalog.querySelectorAll("input[data-plugin-id]"),
      function (input) {
        input.disabled = !selectionAllowed;
      }
    );
    renderPluginSummary(catalog, plugins, active);
    return;
  }
  pluginCatalogRenderKey = renderKey;
  elements.pluginCatalog.textContent = "";
  if (!plugins.length) {
    var empty = document.createElement("p");
    empty.className = "muted help";
    empty.textContent = "No packaged plugins found.";
    elements.pluginCatalog.appendChild(empty);
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
      pluginSelectionDraft = selectedPluginIdsFromView();
      pluginSelectionDraftDigest = catalog ? catalog.digest : null;
      renderPluginSummary(catalog, plugins, pluginSelectionDraft);
      queuePluginSelection(pluginSelectionDraft);
    });
    item.appendChild(checkbox);
    var copy = document.createElement("span");
    var title = document.createElement("strong");
    title.textContent = text(plugin.name, plugin.id);
    copy.appendChild(title);
    if (plugin.description) title.title = plugin.description;
    item.appendChild(copy);
    elements.pluginCatalog.appendChild(item);
  });
  renderPluginSummary(catalog, plugins, active);
}
