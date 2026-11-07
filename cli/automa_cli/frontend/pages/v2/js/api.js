// The one way the page reaches the viewer, named as the viewer names its routes (reads and
// writes in viewer_commands.py):
//   read(route)              GET: the document the command's --json prints; docs[route] keeps
//                            the last one read, so a read that fails leaves the window as it was
//   write(route, body, opts) POST: the command's result with `text`, what it prints;
//                            opts.onLine(line) hears each line where the route streams them
// A refusal answers like a result: {ok: false, error}. mock.js answers the routes it lists
// (opened from disk, every route); the rest go to the viewer.
"use strict";

const routes = {
  plugins: (host) => (host ? `/api/plugins?host_name=${encodeURIComponent(host)}` : "/api/plugins"),
  latest: (host) => `/api/latest?host_name=${encodeURIComponent(host)}`,
};

const api = (() => {
  const docs = {};
  const mocked = (method, route) => typeof mock !== "undefined" && mock.answers(method, route);

  const refused = (error) => ({ ok: false, error });
  // The viewer's answer; one that failed without a refusal's shape, or no answer, becomes one.
  async function fetchJson(route, init) {
    try {
      const response = await fetch(route, { cache: "no-store", ...init });
      const answer = await response.json();
      return response.ok || answer.ok === false ? answer : refused(answer.error || `${route} answered ${response.status}`);
    } catch (error) {
      return refused(String(error.message || error));
    }
  }
  async function read(route) {
    const doc = mocked("GET", route) ? await mock.answer("GET", route) : await fetchJson(route);
    if (doc.ok !== false) docs[route] = doc;
    return doc;
  }
  function write(route, body, { onLine } = {}) {
    if (mocked("POST", route)) return mock.answer("POST", route, body, onLine);
    return fetchJson(route, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  return { docs, read, write };
})();
