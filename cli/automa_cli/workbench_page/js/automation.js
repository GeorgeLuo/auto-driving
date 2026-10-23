(function () {
  var automation = document.getElementById("automationToggle");
  if (!automation) return;

  function apply(state) {
    var running = !!(state && state.running);
    automation.hidden = !running;
    var on = running && !!state.automation;
    automation.classList.toggle("is-on", on);
    automation.setAttribute("aria-pressed", on ? "true" : "false");
    automation.classList.remove("is-busy");
    if (!state || state.ok !== false) automation.title = "";
  }

  function refresh() {
    return fetch("/automation").then(function (response) {
      return response.json();
    }).then(apply).catch(function () {
      apply(null);
    });
  }

  automation.addEventListener("click", function () {
    if (automation.hidden || automation.classList.contains("is-busy")) return;
    var next = automation.getAttribute("aria-pressed") !== "true";
    automation.classList.add("is-busy");
    automation.title = "";
    fetch("/automation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ automation: next })
    }).then(function (response) {
      return response.json().then(function (payload) {
        return { ok: response.ok, payload: payload };
      });
    }).then(function (result) {
      var payload = result.payload || {};
      if (!result.ok || !payload.ok) {
        automation.classList.remove("is-busy");
        automation.title = payload.error || "automation did not change";
        return;
      }
      apply(payload);
    }).catch(function () {
      automation.classList.remove("is-busy");
      automation.title = "automation did not change";
    });
  });

  refresh();
})();
