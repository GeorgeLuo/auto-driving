const fs = require("fs"), vm = require("vm");
const sent = [], replies = [];
const node = () => ({ addEventListener() {}, getAttribute() { return "false"; }, setAttribute() {}, replaceWith() {} });
const elements = new Proxy({}, { get: (t, k) => (k in t ? t[k] : (t[k] = node())) });
const snapshot = (phase) => ({ phase, run_id: "run-1", controls: { allowed_actions: [] } });
let pluginToggleWaiting = false;
const ctx = {
  elements, state: null, actionInFlight: false, stateRequestGeneration: 0,
  imageRequestGeneration: 0, lastImageKey: "", loadedImageKey: "",
  Image: function () {}, window: { addEventListener() {} },
  text: (v) => String(v), setText() {}, setNotice() {}, setButton() {}, renderPlugins() {},
  selectedRunId: () => "run-1", clearRecordSelection() {}, schedulePoll() {}, pollDelay: () => 0,
  settlePluginDraft() {}, revertPluginDraft() {},
  render(next) { if (next) ctx.state = next; },
  flushPluginSelection() {
    if (!pluginToggleWaiting || ctx.actionInFlight) return;
    pluginToggleWaiting = false;
    ctx.action("select_plugins", { step: "perception", active_plugin_ids: ["a"] });
  },
  fetch(url, init) {
    const body = JSON.parse(init.body);
    sent.push(body.action === "seek" ? "seek:" + body.position : body.action);
    return new Promise((resolve) => replies.push((phase) => resolve({
      ok: true, json: async () => ({ ok: true, state: snapshot(phase) }),
    })));
  },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("cli/automa_cli/workbench_page/js/transport.js", "utf8"), ctx);
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) { console.log("FAIL", m, JSON.stringify(a), JSON.stringify(b)); process.exit(1); } };
const settle = async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setImmediate(r)); };
const reply = async (phase) => { replies.shift()(phase); await settle(); };
const scrubTo = (position) => { elements.seekBar.value = String(position); ctx.onSeekInput(); };
elements.seekBar.max = "9";

(async () => {
  // a seek made during an unrelated request is sent once that request settles
  ctx.state = snapshot("paused");
  ctx.action("step");
  ctx.beginScrub(); scrubTo(4); ctx.endScrub();
  eq(sent, ["step"], "seek waits for the request in flight");
  await reply("paused");
  eq(sent, ["step", "seek:4"], "queued seek sent after the request settled");
  await reply("paused");
  eq(sent.length, 2, "nothing else sent");

  // a resume waiting behind the seek survives a plugin selection sent first
  sent.length = 0;
  ctx.state = snapshot("running");
  ctx.beginScrub(); scrubTo(3);
  eq(sent, ["pause"], "scrubbing a running replay pauses it first");
  await reply("paused");
  eq(sent, ["pause", "seek:3"], "seek sent after the pause");
  ctx.endScrub();
  pluginToggleWaiting = true;
  await reply("paused");
  eq(sent, ["pause", "seek:3", "select_plugins"], "plugin selection goes out after the seek");
  eq(ctx.pendingResume, true, "resume still pending while the selection is in flight");
  await reply("paused");
  eq(sent, ["pause", "seek:3", "select_plugins", "resume"], "resume sent once the selection settled");
  await reply("running");
  eq(ctx.pendingResume, false, "nothing left pending");
  console.log("seek ok");
})();
