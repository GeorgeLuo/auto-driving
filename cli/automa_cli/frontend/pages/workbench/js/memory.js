// Memory region: plugins, records, search, and selection.
// Classic script: the page's files share one global scope.
"use strict";

var selectedRecordId = null;
var selectedMemoryPluginId = null;
var memoryListSignature = "";
var memoryPluginSignature = "";
// steps.memory is the memory step's report: every applied plugin by plugin_id,
// and the evidence publisher. The panel lists every plugin and shows one
// plugin's ledger: the plugin the viewer picked, else the evidence publisher.
// With neither, it asks for a pick.
function memoryPlugins(memory) {
  var list = memory && Array.isArray(memory.plugins) ? memory.plugins : [];
  return list.filter(function (entry) {
    return entry && typeof entry.plugin_id === "string";
  });
}
function memoryEvidencePublisher(memory) {
  var publisher = memory && memory.evidence_publisher;
  return typeof publisher === "string" && publisher ? publisher : null;
}
function shownMemoryPlugin(memory) {
  var wanted = selectedMemoryPluginId || memoryEvidencePublisher(memory);
  return memoryPlugins(memory).find(function (entry) {
    return entry.plugin_id === wanted;
  }) || null;
}
function shownMemoryState(memory) {
  var entry = shownMemoryPlugin(memory);
  return entry && entry.state && typeof entry.state === "object" ? entry.state : null;
}
function memoryPluginText(entry, publisher) {
  var count = entry.state ? entry.state.record_count : null;
  return entry.plugin_id + " · " + text(count, "?") + " records"
    + (entry.plugin_id === publisher ? " · publisher" : "");
}
function renderMemoryPlugins(memory) {
  var plugins = memoryPlugins(memory);
  var publisher = memoryEvidencePublisher(memory);
  var shown = shownMemoryPlugin(memory);
  var shownId = shown ? shown.plugin_id : null;
  var signature = plugins.map(function (entry) {
    return memoryPluginText(entry, publisher);
  }).join("\n") + "\0" + String(shownId);
  if (signature === memoryPluginSignature) return;
  memoryPluginSignature = signature;
  var focusedId = elements.memoryPlugins.contains(document.activeElement)
    ? document.activeElement.getAttribute("data-plugin-id") : null;
  elements.memoryPlugins.textContent = "";
  plugins.forEach(function (entry) {
    var isShown = entry.plugin_id === shownId;
    var button = document.createElement("button");
    button.type = "button";
    button.className = "text-action" + (isShown ? " selected" : "");
    button.setAttribute("data-plugin-id", entry.plugin_id);
    button.setAttribute("aria-pressed", isShown ? "true" : "false");
    button.textContent = memoryPluginText(entry, publisher);
    elements.memoryPlugins.appendChild(button);
    if (entry.plugin_id === focusedId) button.focus();
  });
}
function selectMemoryPlugin(pluginId) {
  var memory = currentPayload("memory");
  var entry = memoryPlugins(memory).find(function (candidate) {
    return candidate.plugin_id === pluginId;
  });
  if (!entry) return;
  selectedMemoryPluginId = pluginId;
  selectedRecordId = null;
  memoryListSignature = "";
  setNotice("Showing memory plugin " + pluginId + ".");
  renderMemory();
}
function memorySearchPattern() {
  var term = String(elements.memorySearch.value || "").trim().toLowerCase();
  if (!term) return null;
  try {
    return new RegExp(term);
  } catch (error) {
    return new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  }
}
function memoryRecordId(record) {
  return text(record.record_id, "unknown record");
}
function memoryRecordLabel(record) {
  return text(record.label, memoryRecordId(record));
}
function memoryRecordTitle(record, records) {
  var label = memoryRecordLabel(record);
  var collisions = 0;
  records.forEach(function (candidate) {
    if (memoryRecordLabel(candidate) === label) collisions += 1;
  });
  if (collisions < 2) return label;
  var plugin = record.origin && record.origin.source_plugin_id;
  return plugin ? label + " · " + plugin : label;
}
function memoryRecordMatches(record, records, pattern) {
  if (!pattern) return true;
  pattern.lastIndex = 0;
  return pattern.test(memoryRecordTitle(record, records).toLowerCase());
}
function paintMemorySelection(records) {
  var selected = records.find(function (record) {
    return memoryRecordId(record) === selectedRecordId;
  });
  Array.prototype.forEach.call(
    elements.memoryRecords.querySelectorAll("button[data-record-id]"),
    function (button) {
      var isSelected = button.getAttribute("data-record-id") === selectedRecordId;
      button.classList.toggle("selected", isSelected);
      button.setAttribute("aria-pressed", isSelected ? "true" : "false");
    }
  );
  setText("memorySelection", selected
    ? "selected · " + memoryRecordTitle(selected, records)
    : "select a record");
  elements.memorySelected.textContent = selected
    ? JSON.stringify({
      record_id: selected.record_id,
      kind: selected.kind,
      label: selected.label,
      confidence: selected.confidence,
      origin: selected.origin,
      properties: selected.properties
    }, null, 2)
    : "";
}
function bindMemoryList() {
  if (elements.memoryRecords.getAttribute("data-bound") === "true") return;
  elements.memoryRecords.setAttribute("data-bound", "true");
  elements.memoryRecords.addEventListener("pointerdown", function (event) {
    var button = event.target.closest("button[data-record-id]");
    if (!button) return;
    event.stopPropagation();
    selectRecord(button.getAttribute("data-record-id"));
  });
  function pickPlugin(event) {
    var button = event.target.closest("button[data-plugin-id]");
    if (!button) return;
    event.stopPropagation();
    selectMemoryPlugin(button.getAttribute("data-plugin-id"));
  }
  elements.memoryPlugins.addEventListener("pointerdown", pickPlugin);
  elements.memoryPlugins.addEventListener("click", function (event) {
    if (event.detail === 0) pickPlugin(event);
  });
}
function clearMemoryLedger() {
  setText("memoryEpoch", null);
  setText("memoryCount", null);
  setText("memoryPolicy", null);
  elements.memoryDrops.hidden = true;
  elements.memoryDrops.open = false;
  setText("memorySelection", "no record selected");
}
function renderMemory() {
  bindMemoryList();
  var report = currentPayload("memory");
  var plugins = memoryPlugins(report);
  // Keep the viewer's pick across frames; drop it only when a report lists
  // plugins and it is not among them.
  if (selectedMemoryPluginId && plugins.length && !plugins.some(function (entry) {
    return entry.plugin_id === selectedMemoryPluginId;
  })) {
    selectedMemoryPluginId = null;
  }
  renderMemoryPlugins(report);
  setText("memoryPublisher", memoryEvidencePublisher(report), report ? "none" : "—");
  if (!report) {
    var updateFailed = state && state.phase === "failed" &&
      state.failure_boundary === "memory";
    var disabled = state && Array.isArray(state.active_memory_plugin_ids) &&
      state.active_memory_plugin_ids.length === 0;
    memoryListSignature = "";
    elements.memoryRecords.textContent = "";
    setText("memoryHealth", updateFailed ? "update failed" : disabled ? "disabled" : "no frame yet");
    clearMemoryLedger();
    var noSnapshot = document.createElement("p");
    noSnapshot.className = "memory-empty muted help";
    noSnapshot.textContent = updateFailed
      ? "Memory update stopped this replay. See Failure for details."
      : disabled ? "No memory plugins selected." : "";
    elements.memoryRecords.appendChild(noSnapshot);
    elements.memorySelected.textContent = "";
    return;
  }
  if (!shownMemoryPlugin(report)) {
    memoryListSignature = "";
    elements.memoryRecords.textContent = "";
    setText("memoryHealth", plugins.length ? "pick a plugin" : "no plugins");
    clearMemoryLedger();
    var noPublisher = document.createElement("p");
    noPublisher.className = "memory-empty muted help";
    noPublisher.textContent = plugins.length
      ? "No evidence publisher this frame. Pick a plugin to see its records."
      : "";
    elements.memoryRecords.appendChild(noPublisher);
    elements.memorySelected.textContent = "";
    return;
  }
  var memory = shownMemoryState(report) || {};
  var metadata = memory.metadata || {};
  var dropCount = Number(metadata.last_update_drop_count) || 0;
  var conflictCount = Number(metadata.conflict_count) || 0;
  setText("memoryHealth", text(memory.health, "no health")
    + (conflictCount ? " · " + conflictCount + " conflicts total" : "")
    + (dropCount ? " · " + dropCount + " dropped now" : ""));
  setText("memoryEpoch", memory.epoch_id);
  setText("memoryCount", memory.record_count);
  setText("memoryPolicy", metadata.policy);
  elements.memoryDrops.hidden = dropCount === 0;
  if (dropCount) {
    var drops = Array.isArray(metadata.last_update_drops) ? metadata.last_update_drops : [];
    var omitted = Number(metadata.last_update_drops_omitted) || 0;
    setText("memoryDropsSummary", dropCount + " dropped this frame");
    elements.memoryDropsText.textContent = drops.map(function (drop) {
      return drop.record_id + " · " + drop.reason + " · " + drop.action;
    }).join("\n") + (omitted ? "\n" + omitted + " more omitted" : "");
  }
  var records = Array.isArray(memory.records) ? memory.records : [];
  var pattern = memorySearchPattern();
  var visible = records.filter(function (record) {
    return memoryRecordMatches(record, records, pattern);
  });
  var recordIds = records.map(memoryRecordId);
  if (recordIds.indexOf(selectedRecordId) < 0) {
    selectedRecordId = recordIds.length ? recordIds[0] : null;
  }
  var signature = visible.map(function (record) {
    return memoryRecordId(record) + "\t" + memoryRecordTitle(record, records);
  }).join("\n") + "\0" + String(elements.memorySearch.value || "") + "\0" + String(records.length);
  if (signature === memoryListSignature && elements.memoryRecords.querySelector("button[data-record-id], .memory-empty")) {
    paintMemorySelection(records);
    return;
  }
  memoryListSignature = signature;
  elements.memoryRecords.textContent = "";
  visible.forEach(function (record) {
    var recordId = memoryRecordId(record);
    var titleText = memoryRecordTitle(record, records);
    var card = document.createElement("button");
    card.type = "button";
    card.className = "text-action record";
    card.setAttribute("data-record-id", recordId);
    var title = document.createElement("strong");
    title.textContent = titleText;
    card.appendChild(title);
    card.setAttribute("aria-label", "Show memory record " + titleText);
    elements.memoryRecords.appendChild(card);
  });
  if (!records.length) {
    var noRecords = document.createElement("p");
    noRecords.className = "memory-empty muted help";
    noRecords.textContent = "";
    elements.memoryRecords.appendChild(noRecords);
    setText("memorySelection", "no record selected");
    elements.memorySelected.textContent = "";
    return;
  }
  if (!visible.length) {
    var noMatch = document.createElement("p");
    noMatch.className = "memory-empty muted help";
    noMatch.textContent = "No matching records.";
    elements.memoryRecords.appendChild(noMatch);
  }
  paintMemorySelection(records);
}
function selectRecord(recordId) {
  var memory = shownMemoryState(currentPayload("memory"));
  var records = memory && Array.isArray(memory.records) ? memory.records : [];
  var selected = records.find(function (record) {
    return memoryRecordId(record) === recordId;
  });
  if (!selected) return;
  selectedRecordId = recordId;
  setNotice("Showing memory record " + memoryRecordTitle(selected, records) + ".");
  paintMemorySelection(records);
}
elements.memorySearch.addEventListener("input", function () {
  renderMemory();
});
elements.memorySearch.addEventListener("pointerdown", function (event) {
  event.stopPropagation();
});
