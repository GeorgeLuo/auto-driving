// Runs the shared floating-frame script against a fake DOM with one status box
// and checks that the box marks itself scrollable only while its text overflows.
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const observers = [];
function Observer(callback) {
  observers.push(callback);
  this.observe = () => {};
}

const text = { scrollHeight: 20, clientHeight: 40 };
const status = {
  attributes: {},
  querySelector(selector) { return selector === ".float-frame-status-text" ? text : null; },
  setAttribute(name, value) { this.attributes[name] = value; }
};
const ctx = {
  ResizeObserver: Observer,
  MutationObserver: Observer,
  window: { addEventListener() {} },
  document: {
    querySelectorAll(selector) { return selector === ".float-frame-status" ? [status] : []; }
  }
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync("cli/automa_cli/frontend/pages/shared/float.js", "utf8"), ctx);
const [onResize, onMutation] = observers;

assert.equal(status.attributes["data-scrollable"], "false");

text.scrollHeight = 120;
onMutation();
assert.equal(status.attributes["data-scrollable"], "true");

text.clientHeight = 160;
onResize();
assert.equal(status.attributes["data-scrollable"], "false");

console.log("frame status ok");
