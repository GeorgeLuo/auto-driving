const fs = require("fs"), vm = require("vm");
const sent = [];
let finish = null;
const ctx = {
  actionInFlight: false, state: null, elements: {}, text: (v) => String(v),
  action(name, extra) {
    if (ctx.actionInFlight) return;
    ctx.actionInFlight = true;
    sent.push(extra.step + ":" + extra.active_plugin_ids.join(","));
    return new Promise((r) => { finish = () => { ctx.actionInFlight = false; ctx.flushPluginSelection(); r(); }; });
  },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("cli/automa_cli/workbench_page/js/plugins.js", "utf8"), ctx);
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) { console.log("FAIL", m, JSON.stringify(a), JSON.stringify(b)); process.exit(1); } };
const perception = () => ctx.pluginPanels.perception;
const memory = () => ctx.pluginPanels.memory;
const toggle = (step, ids) => { ctx.pluginPanels[step].draft = ids; ctx.queuePluginSelection(step, ids); };

toggle("perception", ["a"]);                      // sent immediately
toggle("perception", ["a", "b"]);                 // queued
toggle("perception", ["b"]);                      // replaces queued
eq(sent, ["perception:a"], "only first sent while in flight");
ctx.settlePluginDraft("perception");              // answer for the first arrives; newer toggle waits
eq(perception().draft, ["b"], "draft kept while a newer toggle is queued");
finish();
eq(sent, ["perception:a", "perception:b"], "latest queued selection sent once, after the first settled");
eq(perception().queued, null, "queue empty");
ctx.settlePluginDraft("perception");
eq(perception().draft, null, "draft released once nothing is queued");
finish();

// each step keeps its own queue; both are sent, one request at a time
sent.length = 0;
toggle("perception", ["p"]);                      // in flight
toggle("memory", ["m1", "m2"]);                   // waits behind it
eq(sent, ["perception:p"], "memory selection waits for the request in flight");
eq(memory().queued, ["m1", "m2"], "memory selection queued");
ctx.settlePluginDraft("perception");
eq(memory().draft, ["m1", "m2"], "settling perception leaves the memory draft");
finish();
eq(sent, ["perception:p", "memory:m1,m2"], "queued memory selection sent after perception settled");
ctx.settlePluginDraft("memory");
finish();
eq(memory().draft, null, "memory draft released");

// rejection drops what is waiting for that step only
sent.length = 0;
toggle("memory", ["c"]);                          // in flight
toggle("memory", ["d"]);                          // queued
toggle("perception", ["e"]);                      // queued
ctx.revertPluginDraft("memory");
eq(memory().queued, null, "revert clears the memory queue");
eq(perception().queued, ["e"], "revert of one step keeps the other step's queue");
finish();
eq(sent, ["memory:c", "perception:e"], "only the other step's toggle goes out after the revert");
// without a step every panel settles and nothing waits
ctx.revertPluginDraft();
eq([perception().queued, memory().queued, perception().draft, memory().draft], [null, null, null, null], "revert without a step clears every panel");
finish();
eq(sent.length, 2, "nothing extra sent after revert");

// the listing says which perception plugins a memory plugin reads, and nothing otherwise
eq(ctx.pluginNeedsText({ perception_plugins: ["a", "b"] }), "needs perception: a, b", "declared perception plugins are listed");
eq(ctx.pluginNeedsText({}), "", "no declaration, no note");
eq(ctx.pluginNeedsText({ perception_plugins: [] }), "", "empty declaration, no note");
console.log("queue ok");
