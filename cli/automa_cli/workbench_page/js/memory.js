// Memory region: records, search, and selection.
// Classic script: the page's files share one global scope.
"use strict";

var selectedRecordId = null;
var memoryListSignature = "";
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
  var plugin = record.provenance && record.provenance.source_plugin_id;
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
      provenance: selected.provenance,
      properties: selected.properties
    }, null, 2)
    : "Select a server-produced memory record to inspect its provenance.";
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
}
function renderMemory() {
  bindMemoryList();
  var memory = currentPayload("memory");
  if (!memory) {
    var updateFailed = state && state.phase === "failed" &&
      state.failure_boundary === "memory" && !selectedFrameDetail;
    memoryListSignature = "";
    elements.memoryRecords.textContent = "";
    setText("memoryHealth", updateFailed ? "update failed" : "no snapshot");
    setText("memoryEpoch", null);
    setText("memoryCount", null);
    setText("memoryPolicy", null);
    elements.memoryDrops.hidden = true;
    elements.memoryDrops.open = false;
    setText("memorySelection", "no record selected");
    var noSnapshot = document.createElement("p");
    noSnapshot.className = "memory-empty muted help";
    noSnapshot.textContent = updateFailed
      ? "Memory update stopped this replay. See Failure for details."
      : "No retained evidence yet.";
    elements.memoryRecords.appendChild(noSnapshot);
    elements.memorySelected.textContent = "Select a server-produced memory record to inspect its provenance.";
    return;
  }
  var metadata = memory.metadata || {};
  var dropCount = Number(metadata.last_update_drop_count) || 0;
  var conflictCount = Number(metadata.conflict_count) || 0;
  setText("memoryHealth", memory.health
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
    noRecords.textContent = "Memory is empty for this frame.";
    elements.memoryRecords.appendChild(noRecords);
    setText("memorySelection", "no record selected");
    elements.memorySelected.textContent = "Memory is empty for this frame.";
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
  var memory = currentPayload("memory");
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
