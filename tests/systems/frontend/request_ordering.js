const assert = require("assert/strict");
const fs = require("fs"), vm = require("vm");
const makeState = (phase, runId = "run-a", extra = {}) => ({
  phase, run_id: runId, controls: { allowed_actions: [], loop: false }, ...extra,
});
const settle = async () => {
  for (let i = 0; i < 5; i++) await new Promise((resolve) => setImmediate(resolve));
};

async function page() {
  const requests = [], notices = [], settledPlugins = [];
  const node = () => ({ addEventListener() {}, getAttribute() { return "false"; }, setAttribute() {} });
  const elements = new Proxy({}, { get: (target, key) => target[key] || (target[key] = node()) });
  const ctx = {
    elements, state: makeState("running"), actionInFlight: false, stateRequestGeneration: 0,
    pollInFlight: false, pollTimer: null,
    window: { addEventListener() {}, setTimeout() { return 1; }, clearTimeout() {} },
    setText() {}, setButton() {}, renderPlugins() {}, clearRecordSelection() {},
    setNotice(message) { notices.push(message); },
    currentFrame() { return ctx.state.current_frame; },
    selectedRunId(action) { return action === "start" ? null : ctx.state.run_id; },
    settlePluginDraft(step) { settledPlugins.push(step); }, revertPluginDraft() {},
    flushPluginSelection() {},
    fetch(url, init) {
      return new Promise((resolve, reject) => requests.push({
        body: init.body ? JSON.parse(init.body) : { action: "poll" }, resolve, reject,
      }));
    },
  };
  vm.createContext(ctx);
  for (const file of ["transport.js", "main.js"]) {
    vm.runInContext(fs.readFileSync("cli/automa_cli/frontend/pages/workbench/js/" + file, "utf8"), ctx);
  }
  ctx.render = (next) => { if (next) ctx.state = next; };
  const take = (action) => {
    const index = requests.findIndex((request) => request.body.action === action);
    assert.notEqual(index, -1, "missing request: " + action);
    return requests.splice(index, 1)[0];
  };
  const reply = async (request, state, ok = true) => {
    request.resolve({ ok, json: async () => request.body.action === "poll" ? state : {
      ok, state, message: ok ? "" : "stale rejection",
    } });
    await settle();
  };
  await reply(take("poll"), ctx.state);
  return { ctx, notices, settledPlugins, take, reply };
}

(async () => {
  // A late Pause must neither resurrect the reset run nor replace a restarted run.
  for (const restart of [false, true]) {
    const { ctx, take, reply } = await page();
    ctx.action("pause");
    const pause = take("pause");
    ctx.action("reset");
    await reply(take("reset"), makeState("idle", null));
    if (restart) {
      ctx.action("start");
      await reply(take("start"), makeState("running", "run-b"));
    }
    const expected = ctx.state;
    await reply(pause, makeState("paused"));
    assert.equal(ctx.state, expected);
    assert.equal(ctx.controlInFlight, false);
    if (restart) {
      ctx.action("set_cadence", { cadence_ms: 10 });
      const cadence = take("set_cadence");
      assert.equal(cadence.body.run_id, "run-b");
      await reply(cadence, makeState("running", "run-b"));
    }
  }

  // A poll can discover another run while a control request is outstanding.
  // Even the partial update during a plugin action must check run identity.
  {
    const { ctx, take, reply } = await page();
    ctx.action("pause");
    const pause = take("pause");
    ctx.poll();
    await reply(take("poll"), makeState("running", "run-b"));
    ctx.action("select_plugins", { step: "perception", active_plugin_ids: ["frame"] });
    const expected = ctx.state;
    await reply(pause, makeState("paused"));
    assert.equal(ctx.state, expected);
    assert.equal(ctx.state.phase, "running");
    assert.equal(ctx.actionInFlight, true);
    await reply(take("select_plugins"), makeState("running", "run-b"));
  }

  // Pause remains concurrent with a slow selection and cannot cancel its result.
  for (const controlFirst of [false, true]) {
    const { ctx, take, reply, settledPlugins } = await page();
    ctx.action("select_plugins", { step: "perception", active_plugin_ids: ["frame"] });
    const selection = take("select_plugins");
    ctx.action("pause");
    const pause = take("pause");
    if (controlFirst) {
      await reply(pause, makeState("paused"));
      assert.equal(ctx.state.phase, "paused");
      assert.equal(ctx.actionInFlight, true);
    }
    await reply(selection, makeState("paused", "run-a", { active_perception_plugin_ids: ["frame"] }));
    if (!controlFirst) await reply(pause, makeState("running"));
    assert.equal(ctx.state.phase, "paused");
    assert.deepEqual(ctx.state.active_perception_plugin_ids, ["frame"]);
    assert.deepEqual(settledPlugins, ["perception"]);
    assert.equal(ctx.actionInFlight, false);
  }

  // Polls begun before or during Pause cannot undo its committed response.
  for (const pollFirst of [false, true]) {
    const { ctx, take, reply } = await page();
    if (pollFirst) ctx.poll();
    ctx.action("pause");
    if (!pollFirst) ctx.poll();
    const poll = take("poll");
    await reply(take("pause"), makeState("paused"));
    await reply(poll, makeState("running"));
    assert.equal(ctx.state.phase, "paused");
  }

  // Loop responses and stale rejections follow the same boundary checks.
  {
    const { ctx, take, reply, notices } = await page();
    ctx.queueLoop(true);
    const loop = take("set_loop");
    ctx.action("pause");
    const pause = take("pause");
    ctx.action("reset");
    await reply(take("reset"), makeState("idle", null));
    await reply(loop, makeState("running", "run-a", { controls: { loop: true } }));
    await reply(pause, makeState("paused"), false);
    assert.equal(ctx.state.run_id, null);
    assert.equal(ctx.state.controls.loop, false);
    assert.equal(notices.includes("stale rejection"), false);
  }

  // A catch-up response from a run replaced by polling cannot replace its state.
  {
    const { ctx, take, reply } = await page();
    ctx.state = makeState("paused");
    ctx.queueSeek(2);
    const seek = take("seek");
    ctx.poll();
    await reply(take("poll"), makeState("running", "run-b"));
    await reply(seek, makeState("paused"));
    assert.equal(ctx.state.run_id, "run-b");
    assert.equal(ctx.state.phase, "running");
    assert.equal(ctx.seekInFlight, false);
  }
  console.log("request ordering ok");
})().catch((error) => { console.error(error); process.exitCode = 1; });
