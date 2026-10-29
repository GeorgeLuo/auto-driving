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
  currentFrame() { return ctx.state && ctx.state.current_frame; },
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
vm.runInContext(fs.readFileSync("cli/automa_cli/frontend/pages/workbench/js/transport.js", "utf8"), ctx);
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
  eq(sent, ["pause"], "dragging previews without sending intermediate seeks");
  ctx.endScrub();
  eq(sent, ["pause", "seek:3"], "final seek sent on release");
  pluginToggleWaiting = true;
  await reply("paused");
  eq(sent, ["pause", "seek:3", "select_plugins"], "plugin selection goes out after the seek");
  eq(ctx.pendingResume, true, "resume still pending while the selection is in flight");
  await reply("paused");
  eq(sent, ["pause", "seek:3", "select_plugins", "resume"], "resume sent once the selection settled");
  await reply("running");
  eq(ctx.pendingResume, false, "nothing left pending");

  // A second drag supersedes an unfinished catch-up; an old answer cannot
  // replace the newer frame or leave the seek marked in flight.
  sent.length = 0;
  ctx.state = snapshot("paused");
  ctx.beginScrub(); scrubTo(8); ctx.endScrub();
  ctx.beginScrub(); scrubTo(2); ctx.endScrub();
  eq(sent, ["seek:8", "seek:2"], "new seek is sent without waiting for catch-up");
  replies[1]("paused"); replies.splice(1, 1); await settle();
  eq(ctx.seekInFlight, false, "latest seek is settled");
  replies.shift()("running"); await settle();
  eq(ctx.state.phase, "paused", "late old response cannot overwrite latest state");

  // pause is sent even while a slower action still owns actionInFlight
  sent.length = 0;
  ctx.actionInFlight = true;
  ctx.state = snapshot("running");
  ctx.action("pause");
  eq(sent, ["pause"], "pause is sent while another action is in flight");
  ctx.actionInFlight = false;
  console.log("seek ok");
})();
