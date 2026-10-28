// The same live selection transitions must render consistently for every step.
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

function element() {
  return {
    children: [], dataset: {}, className: '', textContent: '',
    get classList() {
      const owner = this;
      return {
        add(name) { owner.className = name; },
        remove(name) { owner.className = owner.className.split(' ').filter(item => item !== name).join(' '); },
        contains(name) { return owner.className.split(' ').includes(name); }
      };
    },
    appendChild(child) { this.children.push(child); },
    replaceChildren(...children) { this.children = children; },
    querySelectorAll() { return nodes.get('plugins').children.flatMap(row => row.children.filter(cell => cell.className === 'enabled')); }
  };
}
const nodes = new Map(['status', 'plugins', 'catalog', 'revisions', 'stack'].map(id => [id, element()]));
const steps = ['perception', 'observation', 'memory', 'proposal', 'plan', 'action'];
const definitions = steps.map(step => ({ step, id: `${step}-plugin`, entrypoint: 'packaged:Plugin', metadata: {} }));
let payload = { ok: true, catalog_version: 1, plugins: definitions,
  arming: { status: 'applied', requested: {}, applied: {} } };
for (const step of steps) {
  payload.arming.requested[step] = [definitions.find(plugin => plugin.step === step)];
  payload.arming.applied[step] = [...payload.arming.requested[step]];
}
let failure = null;
let httpOk = true;
const timers = [];
const ctx = {
  document: { getElementById(id) { return nodes.get(id); }, createElement: element },
  fetch: async (url, options) => {
    assert.strictEqual(url, '/api/plugins');
    assert.strictEqual(options.cache, 'no-store');
    if (failure) throw new Error(failure);
    return { ok: httpOk, status: httpOk ? 200 : 503, json: async () => payload };
  },
  setTimeout(callback, delay) { assert.strictEqual(delay, 500); timers.push(callback); }
};
vm.createContext(ctx);
const html = fs.readFileSync('cli/automa_cli/frontend/pages/plugins/index.html', 'utf8');
// The server fills in the steps' run order.
vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1].replace('__PLUGIN_STEPS__', JSON.stringify(steps)), ctx);
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };
async function poll() { assert.strictEqual(timers.length, 1); timers.shift()(); await settle(); }
function cells(plugin) {
  return nodes.get('plugins').children.find(row => row.dataset.step === plugin.step && row.dataset.pluginId === plugin.id).children.map(cell => cell.className === 'enabled' ? cell.children[0].ariaLabel : cell.textContent);
}

(async () => {
  await settle();
  assert.strictEqual(nodes.get('status').hidden, true);
  assert.strictEqual(nodes.get('status').textContent, '');
  for (const plugin of definitions) {
    assert.strictEqual(cells(plugin)[2], 'Enabled');
    assert.strictEqual(cells(plugin)[3], 'packaged');
  }
  for (const step of steps) {
    const uploaded = { step, id: `${step}-uploaded`, entrypoint: 'prototype:Plugin', metadata: { revision: 'revision-1', filename: 'prototype.py' } };
    payload.plugins.push(uploaded);
    payload.catalog_version++;
    await poll();
    assert.strictEqual(cells(uploaded)[2], 'Disabled', 'upload alone does not enable a plugin');
    assert.strictEqual(cells(uploaded)[3], 'uploaded');
    payload.arming.status = 'requested';
    payload.arming.requested[step] = [uploaded];
    await poll();
    assert.strictEqual(cells(uploaded)[2], 'Disabled', 'a pending arm is not applied');
    assert.strictEqual(cells(uploaded)[5], 'revision-1');
    payload.arming.applied[step] = [uploaded];
    payload.arming.status = 'applied';
    await poll();
    assert.strictEqual(cells(uploaded)[2], 'Enabled');
    assert.strictEqual(cells(definitions.find(plugin => plugin.step === step))[2], 'Disabled');
    const next = { ...uploaded, metadata: { ...uploaded.metadata, revision: 'revision-2' } };
    payload.plugins[payload.plugins.indexOf(uploaded)] = next;
    await poll();
    assert.strictEqual(cells(next)[2], 'Enabled', 'enablement is keyed by step and ID, not available revision');
    assert.strictEqual(cells(next)[4], 'revision-2');
    assert.strictEqual(cells(next)[6], 'revision-1');
    payload.arming.status = 'failed';
    payload.arming.error = 'load refused';
    await poll();
    assert.strictEqual(cells(next)[2], 'Enabled', 'failed requests leave applied indicators intact');
    assert.strictEqual(nodes.get('status').hidden, false);
    assert(nodes.get('status').textContent.includes('load refused'));
    payload.arming.status = 'requested';
    payload.arming.error = null;
    payload.arming.requested[step] = [];
    await poll();
    assert.strictEqual(cells(next)[2], 'Enabled', 'pending disarm is still enabled');
    payload.arming.applied[step] = [];
    payload.arming.status = 'applied';
    await poll();
    assert.strictEqual(cells(next)[2], 'Disabled');
  }
  delete payload.arming.applied.observation;
  await poll();
  assert.strictEqual(cells(definitions[1])[2], 'Unknown', 'missing selection data is not disabled');
  failure = 'host unreachable';
  await poll();
  assert(nodes.get('catalog').classList.contains('stale'));
  assert.strictEqual(nodes.get('status').hidden, false);
  assert(nodes.get('status').textContent.includes('Showing stale snapshot'));
  for (const row of nodes.get('plugins').children) assert.strictEqual(row.children[2].children[0].ariaLabel, 'Unknown');
  failure = null;
  httpOk = false;
  await poll();
  assert(nodes.get('status').textContent.includes('HTTP 503'));
  httpOk = true;
  await poll();
  assert(!nodes.get('catalog').classList.contains('stale'));
  assert.strictEqual(nodes.get('status').hidden, true);
  assert.strictEqual(nodes.get('status').textContent, '');
  assert.strictEqual(cells(definitions[0])[2], 'Disabled');
  console.log('plugin catalog state ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
