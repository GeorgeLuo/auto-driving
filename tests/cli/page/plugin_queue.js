const fs = require("fs"), vm = require("vm");
const sent = [];
let finish = null;
const ctx = {
  actionInFlight: false, pluginCatalogRenderKey: "k", state: null, elements: {}, text: (v) => String(v),
  action(name, extra) {
    if (ctx.actionInFlight) return;
    ctx.actionInFlight = true;
    sent.push(extra.active_plugin_ids.slice());
    return new Promise((r) => { finish = () => { ctx.actionInFlight = false; ctx.flushPluginSelection(); r(); }; });
  },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("cli/automa_cli/workbench_page/js/plugins.js", "utf8"), ctx);
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) { console.log("FAIL", m, JSON.stringify(a), JSON.stringify(b)); process.exit(1); } };
ctx.pluginSelectionDraft = ["a"];
ctx.queuePluginSelection(["a"]);                 // sent immediately
ctx.pluginSelectionDraft = ["a", "b"]; ctx.queuePluginSelection(["a", "b"]);   // queued
ctx.pluginSelectionDraft = ["b"]; ctx.queuePluginSelection(["b"]);             // replaces queued
eq(sent, [["a"]], "only first sent while in flight");
ctx.settlePluginDraft();                          // answer for the first arrives; newer toggle waits
eq(ctx.pluginSelectionDraft, ["b"], "draft kept while a newer toggle is queued");
finish();
eq(sent, [["a"], ["b"]], "latest queued selection sent once, after the first settled");
eq(ctx.pluginSelectionQueued, null, "queue empty");
ctx.settlePluginDraft();
eq(ctx.pluginSelectionDraft, null, "draft released once nothing is queued");
// rejection drops what is waiting
ctx.queuePluginSelection(["c"]); ctx.queuePluginSelection(["d"]);
ctx.revertPluginDraft();
eq(ctx.pluginSelectionQueued, null, "revert clears queue");
finish();
eq(sent.length, 2, "nothing extra sent after revert");
console.log("queue ok");
