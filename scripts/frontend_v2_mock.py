"""Click through the frontend v2 mock (cli/automa_cli/frontend/pages/v2/index.html) and check each window.

The page reaches its data only through js/api.js's reads and writes; js/mock.js answers every
route from an in-page store, so nothing here calls the CLI. Needs Playwright, which is not a project dependency:

    pip install playwright && playwright install chromium
    python scripts/frontend_v2_mock.py

Screenshots go to pages/v2/shots/, which git ignores. Prints "ok:" and the shots on success.
"""
import re
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parents[1] / "cli" / "automa_cli" / "frontend" / "pages" / "v2"
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)
errors = []


def frame(page, frame_id):
    return page.locator(f"#{frame_id}")


def status(page, frame_id):
    return frame(page, frame_id).locator(".float-frame-status-text")


def record_pane(page, frame_id):
    """Record the pane's text, and whether the window's first verb is disabled, at every change
    from now: a command's busy state can pass before an expect looks. Returns a reader."""
    page.evaluate("""id => {
      const root = document.getElementById(id), text = root.querySelector(".float-frame-status-text");
      const state = () => [text.textContent, root.querySelector("[data-verbs] .key").disabled];
      const seen = (window.seen ??= {})[id] = [state()];
      new MutationObserver(() => seen.push(state()))
        .observe(root, { subtree: true, childList: true, characterData: true, attributes: true });
    }""", frame_id)
    return lambda: [tuple(state) for state in page.evaluate("id => window.seen[id]", frame_id)]


def exactly(text):
    return re.compile(rf"^{re.escape(text)}$")


def key(page, frame_id, label):
    """A key in the window's pane, bottom right."""
    return frame(page, frame_id).locator("[data-keys] .key:not([hidden])", has_text=exactly(label))


def verb(page, frame_id, label):
    """A verb at the top of the window's list."""
    return frame(page, frame_id).locator("[data-verbs] .key:not([hidden])", has_text=exactly(label))


def plugin_keys(page):
    return [b.inner_text() for b in page.locator("#pluginsFrame [data-keys] .key:not([hidden])").all()]


def row(page, frame_id, name):
    return frame(page, frame_id).locator(".row:not([hidden])", has_text=exactly(name))



def button(page, name):
    return page.locator("main").get_by_role("button", name=name)


def view(page, step, label="VIEW"):
    """Select the step in Plugins and return its pane key: VIEW opens the step's window, STRM streams it."""
    step_row = page.locator("#pluginsFrame .row:not(.child):not([hidden])", has_text=exactly(step))
    if "selected" not in (step_row.get_attribute("class") or ""):
        step_row.click()
    return key(page, "pluginsFrame", label)


def text_top(locator):
    """Top of the first line of text inside an element: its box top plus its top border and padding."""
    return locator.evaluate("""el => { const s = getComputedStyle(el);
      return el.getBoundingClientRect().top + parseFloat(s.borderTopWidth) + parseFloat(s.paddingTop); }""")


def list_level_with_pane(page):
    sims = frame(page, "simulatorsFrame")
    first = sims.locator("[data-verbs] .key").first  # always the top line
    pane = sims.locator(".float-frame-status").bounding_box()["y"]  # the pane's top edge
    assert abs(text_top(first) - pane) <= 1, ("list", text_top(first), "pane", pane)


def clear_of_column(page):
    col = page.locator("main nav").first.bounding_box()
    bottom = page.locator("main nav").last.bounding_box()
    left, right, top, end = col["x"], col["x"] + col["width"], col["y"], bottom["y"] + bottom["height"]
    for open_frame in page.locator(".float-frame:not([hidden])").all():
        box = open_frame.bounding_box()
        overlaps = box["x"] < right and box["x"] + box["width"] > left and box["y"] < end and box["y"] + box["height"] > top
        assert not overlaps or open_frame.get_attribute("id") == "gapsFrame", (open_frame.get_attribute("id"), box)


with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1512, "height": 900})
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: msg.type == "error" and errors.append(msg.text))
    page.goto((HERE / "index.html").as_uri() + "?fast")
    labels = page.locator("main nav .nav").evaluate_all("els => els.map(el => el.firstChild.textContent.trim())")
    assert labels == ["Stream", "Vehicles", "Hosts", "Plugins", "Simulators", "Workbench"], labels
    for readout, text in [("vehicles", "0/0 up"), ("hosts", "0 hosts"), ("plugins", "packaged"), ("simulators", "none")]:
        expect(page.locator(f"#{readout}Readout")).to_have_text(text)
    shot = lambda name: page.screenshot(path=str(SHOTS / f"{name}.png"))
    shot("0-start")
    # Default size: a third of the page tall, twice as wide as tall.
    for name in ["Simulators", "Vehicles", "Hosts", "Plugins"]:
        button(page, name).click()
        box = frame(page, f"{name.lower()}Frame").bounding_box()
        assert (round(box["height"]), round(box["width"])) == (300, 600), (name, box)
        button(page, name).click()

    for frame_id in ["vehiclesFrame", "hostsFrame", "pluginsFrame", "simulatorsFrame"]:
        button(page, frame_id.removesuffix("Frame").title()).click()
        if frame_id == "pluginsFrame":  # the host level: plugins list, here the packaged catalog
            expect(status(page, frame_id)).to_contain_text("Catalog: packaged\n- perception/frame: packaged (default)")
        else:
            expect(status(page, frame_id)).to_have_text("")
    list_level_with_pane(page)
    shot("0b-empty-windows")
    # Pane text is the terminal face; the pane's keys are not.
    family = lambda selector: page.locator(selector).first.evaluate("el => getComputedStyle(el).fontFamily")
    assert family("#simulatorsFrame .float-frame-status-text").startswith("Menlo"), family("#simulatorsFrame .float-frame-status-text")
    assert family("#hostsFrame [data-verbs] .key").startswith("Menlo")  # the pane's face
    # One face inside windows; the title bar keeps the interface font.
    for selector in ["#simulatorsFrame [data-verbs] .key", "#vehiclesFrame [data-verbs] .key", "#pluginsFrame [data-verbs] .key",
                     "#hostsFrame [data-verbs] .key"]:
        assert family(selector).startswith("Menlo"), (selector, family(selector))
    assert not family("#hostsFrame .float-frame-title strong").startswith("Menlo")
    assert page.locator("#simulatorsFrame [data-verbs] .key").evaluate("el => getComputedStyle(el).backgroundColor") == \
        page.locator("#hostsFrame [data-verbs] .key").evaluate("el => getComputedStyle(el).backgroundColor")
    # Buttons are the only rounded shapes.
    radius = lambda selector: page.locator(selector).first.evaluate("el => getComputedStyle(el).borderTopLeftRadius")
    for selector in ["#hostsFrame .float-frame-status", ".float-frame", "#pluginsFrame .float-frame-status", "#hostsFrame .field-select"]:
        assert radius(selector) == "0px", (selector, radius(selector))
    assert radius("#hostsFrame [data-verbs] .key") == "0px"  # highlighted text, not a button
    for selector in ["main button.nav", "#gapsToggle"]:
        assert radius(selector) != "0px", (selector, radius(selector))
    for name in ["Vehicles", "Hosts", "Plugins"]:
        button(page, name).click()
    create_text = verb(page, "simulatorsFrame", "NEW")
    seen = record_pane(page, "simulatorsFrame")
    create_text.click()
    expect(create_text).to_have_text("NEW")
    expect(row(page, "simulatorsFrame", "127.0.0.1:5051")).to_have_text("127.0.0.1:5051")  # the address only
    expect(create_text).to_be_enabled()
    expect(status(page, "simulatorsFrame")).to_have_text("Created Simulator (chase-sim) at http://127.0.0.1:5051/")
    assert ("...", True) in seen(), seen()  # "..." while it runs, NEW disabled
    shot("0bb-simulator-created")
    expect(page.locator("#simulatorsReadout")).to_have_text("1 created")
    # The list on the left, "create" under it, the pane to its right.
    listed = frame(page, "simulatorsFrame").locator(".list").bounding_box()
    create = verb(page, "simulatorsFrame", "NEW").bounding_box()
    pane = frame(page, "simulatorsFrame").locator(".float-frame-status").bounding_box()
    first_row = row(page, "simulatorsFrame", "127.0.0.1:5051").bounding_box()
    assert first_row["y"] >= create["y"] + create["height"], (create, first_row)  # the list builds under "create"
    assert pane["x"] >= listed["x"] + listed["width"], (pane, listed)
    row(page, "simulatorsFrame", "127.0.0.1:5051").click()
    expect(status(page, "simulatorsFrame")).to_have_text(
        "Simulator (chase-sim)  running at http://127.0.0.1:5051/\n  frontend connected, game chase\n"
        f"  created by simulators create, pid {page.evaluate('mock.store.simulators[0].pid')}")
    expect(key(page, "simulatorsFrame", "DEL")).to_be_visible()
    list_level_with_pane(page)
    list_w = frame(page, "simulatorsFrame").locator(".list").bounding_box()["width"]
    pane_w = frame(page, "simulatorsFrame").locator(".float-frame-status").bounding_box()["width"]
    assert abs(pane_w - 2 * list_w) <= 2, ("thirds", list_w, pane_w)
    shot("0c-simulators")
    row(page, "simulatorsFrame", "127.0.0.1:5051").click()
    expect(key(page, "simulatorsFrame", "DEL")).to_have_count(0)
    button(page, "Vehicles").click()
    expect(row(page, "vehiclesFrame", "127.0.0.1:5051")).to_have_text("127.0.0.1:5051")
    row(page, "vehiclesFrame", "127.0.0.1:5051").click()
    expect(status(page, "vehiclesFrame")).to_have_text(
        "Simulator (chase-sim)  running at http://127.0.0.1:5051/\n  frontend connected, game chase\n"
        f"  created by simulators create, pid {page.evaluate('mock.store.simulators[0].pid')}")
    shot("0d-vehicle-status")
    # A simulator is removed like any vehicle, stays in Simulators, and is discovered again.
    key(page, "vehiclesFrame", "UNTRK").click()
    key(page, "vehiclesFrame", "YES").click()
    expect(row(page, "vehiclesFrame", "127.0.0.1:5051")).to_have_count(0)
    expect(row(page, "simulatorsFrame", "127.0.0.1:5051")).to_have_count(1)
    expect(page.locator("#vehiclesReadout")).to_have_text("0/0 up")
    verb(page, "vehiclesFrame", "DISC").click()
    frame(page, "vehiclesFrame").locator("[data-field=provider]").select_option("chase-sim")
    frame(page, "vehiclesFrame").locator("[data-field=url]").fill("http://127.0.0.1:5051/")
    key(page, "vehiclesFrame", "DISC").click()
    expect(status(page, "vehiclesFrame")).to_have_text("Discovered Simulator (chase-sim) at http://127.0.0.1:5051/")
    expect(row(page, "vehiclesFrame", "127.0.0.1:5051")).to_have_count(1)
    expect(page.locator("#vehiclesReadout")).to_have_text("1/1 up")
    expect(page.locator("#vehiclesReadout")).to_have_text("1/1 up")
    clear_of_column(page)

    # Vehicles: "discover" heads the list; its flags fill the pane, and its output streams there.
    vehicles = frame(page, "vehiclesFrame")
    discover = verb(page, "vehiclesFrame", "DISC")
    pane_text = lambda frame_id: frame(page, frame_id).locator(".float-frame-status").bounding_box()["y"]
    assert abs(discover.bounding_box()["y"] - pane_text("vehiclesFrame")) <= 1, "discover level with the pane"
    discover.click()
    form = vehicles.locator(".float-frame-status [data-form=discover]")
    expect(form).to_be_visible()
    expect(row(page, "vehiclesFrame", "127.0.0.1:5051")).to_be_visible()  # the list stays beside the form
    expect(key(page, "vehiclesFrame", "DISC")).to_be_disabled()
    form.locator("[data-field=url]").fill("http://10.0.0.9:8887")
    key(page, "vehiclesFrame", "DISC").click()
    expect(status(page, "vehiclesFrame")).to_have_text("No PiCar can take a host at http://10.0.0.9:8887: GET http://10.0.0.9:8887/host/status did not answer within 5 s")
    discover.click()
    form.locator("[data-field=url]").fill("http://piracer.local:8887")
    assert key(page, "vehiclesFrame", "CANCEL").get_attribute("data-tone") == "remove"
    assert key(page, "vehiclesFrame", "DISC").get_attribute("data-tone") == ""
    shot("0e-discover-form")
    seen = record_pane(page, "vehiclesFrame")
    key(page, "vehiclesFrame", "DISC").click()
    expect(status(page, "vehiclesFrame")).to_have_text("Discovered PiCar (picar) at http://piracer.local:8887")
    assert ("...", True) in seen(), seen()
    expect(page.locator("#vehiclesReadout")).to_have_text("2/2 up")
    row(page, "vehiclesFrame", "piracer.local:8887").click()
    expect(status(page, "vehiclesFrame")).to_have_text("PiCar (picar)  reachable, no host listening at http://piracer.local:8887")
    expect(key(page, "vehiclesFrame", "UNTRK")).to_be_visible()
    shot("0f-discovered")
    key(page, "vehiclesFrame", "UNTRK").click()
    expect(status(page, "vehiclesFrame")).to_have_text("Untrack?")
    key(page, "vehiclesFrame", "YES").click()
    expect(row(page, "vehiclesFrame", "piracer.local:8887")).to_have_count(0)
    expect(vehicles.locator("[data-keys] .key:not([hidden])")).to_have_count(0)  # nothing selected, no keys

    # Hosts: HOST on a vehicle with no host opens a new host's form filled in for it.
    if frame(page, "vehiclesFrame").is_hidden():
        button(page, "Vehicles").click()
    vrow = row(page, "vehiclesFrame", "127.0.0.1:5051")
    if "selected" not in (vrow.get_attribute("class") or ""):
        vrow.click()
    key(page, "vehiclesFrame", "HOST").click()
    expect(frame(page, "hostsFrame")).to_be_visible()
    create_form = frame(page, "hostsFrame").locator(".float-frame-status [data-form=create]")
    expect(create_form).to_be_visible()
    expect(create_form.locator("[data-field=vehicle]")).to_have_value("http://127.0.0.1:5051/")
    key(page, "hostsFrame", "CANCEL").click()
    hosts_new = verb(page, "hostsFrame", "NEW")
    expect(frame(page, "hostsFrame").locator("[data-keys] .key:not([hidden])")).to_have_count(0)
    hosts_new.click()
    expect(create_form).to_be_visible()
    expect(key(page, "hostsFrame", "NEW")).to_be_disabled()  # no name yet
    create_form.locator("[data-field=name]").fill("chaser")
    # The render tick leaves the vehicle dropdown alone, so an open one stays open.
    mutations = create_form.locator("[data-field=vehicle]").evaluate("""select => new Promise((done) => {
      let n = 0; const seen = new MutationObserver((records) => { n += records.length; });
      seen.observe(select, { subtree: true, childList: true, attributes: true, characterData: true });
      setTimeout(() => { seen.disconnect(); done(n); }, 2500);
    })""")
    assert mutations == 0, f"vehicle dropdown rewritten {mutations} times while open"
    shot("1-create-host")
    key(page, "hostsFrame", "NEW").click()
    expect(status(page, "hostsFrame")).to_contain_text("Host chaser created for the chaser at http://127.0.0.1:5051/")
    expect(frame(page, "hostsFrame").locator("[data-form=create]")).to_be_hidden()
    expect(row(page, "hostsFrame", "chaser")).to_have_text("chaser")
    # A second host by the same name answers with the error, under the open form.
    hosts_new.click()
    create_form.locator("[data-field=name]").fill("chaser")
    key(page, "hostsFrame", "NEW").click()
    expect(status(page, "hostsFrame")).to_have_text("Host chaser already exists.")
    expect(status(page, "hostsFrame")).to_be_visible()
    key(page, "hostsFrame", "CANCEL").click()

    # Selecting a host shows hosts status --name; no other window follows it.
    row(page, "hostsFrame", "chaser").click()
    expect(status(page, "hostsFrame")).to_contain_text(
        "Host chaser  not started\n  chaser at http://127.0.0.1:5051/\n  0 decisions, 0 errors\n  attached since ")
    labels = lambda: [b.inner_text() for b in frame(page, "hostsFrame").locator("[data-keys] .key:not([hidden])").all()]
    assert labels() == ["RUN", "UPD", "DEL"], labels()
    expect(row(page, "hostsFrame", "chaser")).to_have_class("row selected")
    expect(page.locator("#hostsReadout")).to_have_text("1 host")
    expect(page.locator("#pluginsReadout")).to_have_text("packaged")
    shot("2-selected")
    # HOST on a vehicle that has one selects that host in Hosts.
    row(page, "hostsFrame", "chaser").click()  # deselect
    vrow = row(page, "vehiclesFrame", "127.0.0.1:5051")
    if "selected" not in (vrow.get_attribute("class") or ""):
        vrow.click()
    key(page, "vehiclesFrame", "HOST").click()
    expect(row(page, "hostsFrame", "chaser")).to_have_class("row selected")
    expect(status(page, "hostsFrame")).to_contain_text("Host chaser  not started")

    key(page, "hostsFrame", "RUN").click()
    run_form = frame(page, "hostsFrame").locator(".float-frame-status [data-form=run]")
    expect(run_form).to_be_visible()
    expect(frame(page, "hostsFrame").locator(".float-frame-title [data-mode]")).to_have_text("run")
    run_form.locator("[data-field=record]").select_option("on")
    expect(status(page, "hostsFrame")).to_be_hidden()  # only the form while it is open
    shot("3-run-form")
    key(page, "hostsFrame", "RUN").click()
    expect(status(page, "hostsFrame")).to_contain_text("Decisions: unbounded")
    # RUN opens a Stream window on the host, in perception.
    expect(frame(page, "streamFrame1")).to_be_visible()
    expect(frame(page, "streamFrame1").locator(".float-frame-title [data-mode]")).to_contain_text("chaser · perception")
    assert labels() == ["STOP", "RSTRT", "UPD", "DEL"], labels()
    tones = [b.get_attribute("data-tone") for b in frame(page, "hostsFrame").locator("[data-keys] .key:not([hidden])").all()]
    assert tones == ["", "", "", "remove"], tones
    bg = lambda label: key(page, "hostsFrame", label).evaluate("el => getComputedStyle(el).backgroundColor")
    assert bg("DEL") != bg("UPD"), "DEL is tinted"
    # RSTRT takes run's form again: hosts restart does not keep the last run's flags.
    key(page, "hostsFrame", "RSTRT").click()
    expect(run_form).to_be_visible()
    expect(frame(page, "hostsFrame").locator(".float-frame-title [data-mode]")).to_have_text("restart")
    assert labels() == ["RSTRT", "CANCEL"], labels()
    key(page, "hostsFrame", "CANCEL").click()
    assert labels() == ["STOP", "RSTRT", "UPD", "DEL"], labels()

    # Plugins: HOSTS and UPLD head the step list; a step shows plugins list --step.
    button(page, "Plugins").click()
    # Plugins starts on the packaged catalog; HOSTS picks its host.
    expect(verb(page, "pluginsFrame", "UPLD")).to_be_disabled()
    mode = lambda frame_id: frame(page, frame_id).locator(".float-frame-title [data-mode]")
    expect(mode("pluginsFrame")).to_have_text("packaged · catalog")
    verb(page, "pluginsFrame", "HOSTS").click()
    expect(mode("pluginsFrame")).to_have_text("choose host")
    expect(row(page, "pluginsFrame", "packaged")).to_have_class("row selected")
    shot("4-plugins-hosts")
    row(page, "pluginsFrame", "chaser").click()
    expect(page.locator("#pluginsReadout")).to_have_text("chaser")
    expect(mode("pluginsFrame")).to_have_text("chaser · catalog")
    expect(row(page, "pluginsFrame", "perception")).to_be_visible()
    expect(verb(page, "pluginsFrame", "UPLD")).to_have_text("UPLD")
    expect(verb(page, "pluginsFrame", "UPLD")).to_be_enabled()
    # VIEW opens a Stream window on the host, in the step.
    for n, step in enumerate(["memory", "proposal", "plan", "action"], start=2):
        view(page, step).click()
        expect(frame(page, f"streamFrame{n}").locator(".float-frame-title [data-mode]")).to_contain_text(f"chaser · {step}")
        expect(frame(page, f"streamFrame{n}").locator("[data-feed]")).to_be_hidden()
        frame(page, f"streamFrame{n}").locator("[data-frame-close]").click()
    # STRM is plugins stream --step's terminal screen in the pane; STOP ends it.
    view(page, "memory", "STRM").click()
    expect(frame(page, "pluginsFrame").locator(".float-frame-title [data-mode]")).to_have_text("chaser · memory stream")
    expect(status(page, "pluginsFrame")).to_contain_text("Live memory: chaser [live]")
    expect(status(page, "pluginsFrame")).to_contain_text("bounded_evidence: health=healthy epoch=epoch-1 records=")
    view(page, "plan", "STRM").click()
    expect(status(page, "pluginsFrame")).to_contain_text("Plan stream: chaser frame=chase_frame_")
    expect(status(page, "pluginsFrame")).to_contain_text("Plan: status=selected selector=highest_confidence")
    view(page, "perception", "STRM").click()
    expect(status(page, "pluginsFrame")).to_contain_text("automa perception stream\n\nhost: chaser")
    expect(status(page, "pluginsFrame")).to_contain_text("plugins=frame,floor_plane\n")
    assert "multi_obstruction_tracks" not in status(page, "pluginsFrame").inner_text(), "only armed plugins' lines"
    shot("4a-perception-stream")
    key(page, "pluginsFrame", "STOP").click()
    # Stream: every click is one more window; each picks its own host and step.
    page.locator("#streamOpen").click()
    # A new window opens on its feeds: the hosts, none chosen; choosing one shows its steps and status.
    feeds = frame(page, "streamFrame6")
    expect(feeds.locator(".float-frame-title [data-mode]")).to_have_text("feeds")
    expect(feeds.locator(".row.child:not([hidden])")).to_have_count(0)
    expect(status(page, "streamFrame6")).to_have_text("")
    row(page, "streamFrame6", "chaser").click()
    expect(feeds.locator(".row.child:not([hidden])")).to_have_count(5)
    expect(feeds.locator(".row.marked")).to_have_count(0)
    expect(status(page, "streamFrame6")).to_contain_text("Host chaser  running")
    expect(status(page, "streamFrame6")).to_contain_text("Perception: frame, floor_plane")
    shot("4b-feeds")
    row(page, "streamFrame6", "perception").click()
    expect(feeds.locator(".float-frame-title [data-mode]")).to_contain_text("chaser · perception")
    expect(feeds.locator(".list-rows")).to_be_hidden()
    expect(feeds.locator("[data-aside]")).to_be_visible()
    page.wait_for_function(
        "() => { const c = document.querySelector('#streamFrame6 .feed-stage canvas'); if (!c || !c.width) return false;"
        " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 4) if (d[i]) return true; return false; }",
        timeout=10000)
    page.locator("#streamOpen").click()
    expect(page.locator(".stream-frame:not([hidden])")).to_have_count(3)
    expect(page.locator("#streamReadout")).to_have_text("3 open")
    row(page, "streamFrame7", "chaser").click()
    row(page, "streamFrame7", "plan").click()
    expect(status(page, "streamFrame7")).to_contain_text("Plan stream: chaser frame=chase_frame_")
    expect(frame(page, "streamFrame7").locator("[data-step-meta]")).to_contain_text("Plugins")
    # PLGS opens Plugins on the stream's host and step.
    verb(page, "streamFrame7", "PLGS").click()
    expect(frame(page, "pluginsFrame")).to_be_visible()
    expect(frame(page, "pluginsFrame").locator(".float-frame-title [data-mode]")).to_have_text("chaser · catalog")
    expect(row(page, "pluginsFrame", "plan")).to_have_class(re.compile(r"\bselected\b"))
    expect(status(page, "pluginsFrame")).to_contain_text("Plan (most_confident preset), one plugin")
    expect(frame(page, "streamFrame6").locator(".kicker", has_text="Overlays")).to_have_count(0)
    row(page, "pluginsFrame", "perception").click()  # back to perception for the checks below
    # FEEDS goes back to the selection; FEEDS there returns to the stream.
    verb(page, "streamFrame7", "FEEDS").click()
    expect(frame(page, "streamFrame7").locator(".float-frame-title [data-mode]")).to_have_text("feeds")
    expect(verb(page, "streamFrame7", "PLGS")).to_be_disabled()
    expect(frame(page, "streamFrame7").locator("[data-step-meta]")).to_be_hidden()
    verb(page, "streamFrame7", "FEEDS").click()
    expect(frame(page, "streamFrame7").locator(".float-frame-title [data-mode]")).to_contain_text("chaser · plan")
    expect(frame(page, "streamFrame7").locator("[data-feed]")).to_be_hidden()
    expect(frame(page, "streamFrame7").locator("[data-aside]")).to_be_hidden()
    expect(frame(page, "streamFrame6").locator("[data-feed]")).to_be_visible()
    key(page, "streamFrame7", "PAUSE").click()
    paused = status(page, "streamFrame7").inner_text()
    page.wait_for_timeout(700)
    assert status(page, "streamFrame7").inner_text() == paused, "a paused stream holds its frame"
    shot("4b-streams")
    col = page.locator("main").bounding_box()
    for n in (6, 7):
        assert frame(page, f"streamFrame{n}").bounding_box()["x"] >= col["x"] + col["width"], "streams open clear of the launchers"
    page.locator("#streamFrame7 [data-frame-close]").click()
    expect(page.locator("#streamFrame7")).to_have_count(0)
    expect(page.locator("#streamReadout")).to_have_text("2 open")
    page.locator("#streamFrame6 [data-frame-close]").click()
    expect(status(page, "pluginsFrame")).to_contain_text("Perception (lightweight_observer preset)\nCatalog: host chaser\n* frame (packaged) default")
    children = frame(page, "pluginsFrame").locator(".row.child:not([hidden])")
    expect(children).to_have_count(13)  # the packaged perception catalog
    expect(children.filter(has_text="floor_plane")).to_have_class("row marked child")
    run_stream = frame(page, "streamFrame1")
    expect(run_stream.locator(".feed-stage img")).to_be_visible()
    expect(run_stream.locator(".float-frame-title [data-mode]")).to_contain_text("chase_frame_")
    # The v1 page's column, under the steps: metrics, the armed plugins' runs, and their findings.
    expect(run_stream.locator("[data-metrics] dd[data-metric=Resolution]")).to_have_text("640 x 480")
    expect(run_stream.locator("[data-runs] .aside-row:not([hidden])").first).to_contain_text("frame")
    # Frames change every tick; wait for the one shown to have loaded.
    page.wait_for_function("() => document.querySelector('#streamFrame1 .feed-stage img').naturalWidth === 640", timeout=5000)
    # The frame fills the pane's height: no bar under it.
    img, pane = run_stream.locator(".feed-stage img").bounding_box(), run_stream.locator("[data-feed]").bounding_box()
    assert abs(img["height"] - pane["height"]) <= 3, (img, pane)
    run_stream.screenshot(path=str(SHOTS / "4c-stream-perception.png"))
    # Not every recorded frame has a finding the default plugins draw; wait for one that does.
    page.wait_for_function(
        "() => { const c = document.querySelector('#streamFrame1 .feed-stage canvas'); if (!c.width) return false; const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 4) if (d[i]) return true; return false; }",
        timeout=10000)
    clear_of_column(page)
    shot("4-running")

    button(page, "Vehicles").click()
    children.filter(has_text="motion_tracks").click()
    expect(status(page, "pluginsFrame")).to_contain_text("perception/motion_tracks (packaged)\n  not armed")
    key(page, "pluginsFrame", "ARM").click()
    expect(status(page, "pluginsFrame")).to_contain_text("Applied perception: frame (packaged), floor_plane (packaged), motion_tracks (packaged)")
    expect(children.filter(has_text="motion_tracks")).to_have_class(re.compile(r"\bmarked\b"))
    expect(key(page, "pluginsFrame", "DSARM")).to_have_attribute("data-tone", "remove")
    # Management is the same window, by level. The host: plugins list --long, SAVE and DSALL.
    row(page, "pluginsFrame", "perception").click()  # from a plugin back to the host
    expect(status(page, "pluginsFrame")).to_contain_text("Host: chaser\nCatalog version: 1")
    expect(status(page, "pluginsFrame")).to_contain_text("- perception/motion_tracks: packaged (armed)")
    assert plugin_keys(page) == ["SAVE", "DSALL"], plugin_keys(page)
    # A step: PRST, STRM, VIEW, DSALL.
    row(page, "pluginsFrame", "perception").click()
    assert plugin_keys(page) == ["PRST", "STRM", "VIEW", "DSALL"], plugin_keys(page)
    key(page, "pluginsFrame", "PRST").click()
    expect(mode("pluginsFrame")).to_have_text("chaser · perception preset")
    frame(page, "pluginsFrame").locator("[data-field=preset]").select_option("obstruction_observer")
    expect(status(page, "pluginsFrame")).to_have_text("plugins: frame, floor_plane, multi_obstruction_tracks")
    shot("5a-plugins-preset")
    key(page, "pluginsFrame", "ARM").click()
    expect(status(page, "pluginsFrame")).to_contain_text("Applied perception: frame (packaged), floor_plane (packaged), multi_obstruction_tracks (packaged)")
    expect(children.filter(has_text="multi_obstruction_tracks")).to_have_class(re.compile(r"\bmarked\b"))
    # UPLD: plugins upload, its step taken from the selection.
    plugin_file = Path(tempfile.mkdtemp()) / "prototype.py"  # outside the package the boundary tests scan
    plugin_file.write_text("class Prototype:\n    pass\n")
    verb(page, "pluginsFrame", "UPLD").click()
    upload = frame(page, "pluginsFrame").locator("[data-form=upload]")
    expect(mode("pluginsFrame")).to_have_text("chaser · upload")
    expect(upload.locator("[data-field=step]")).to_have_value("perception")
    expect(key(page, "pluginsFrame", "UPLD")).to_be_disabled()
    upload.locator("[data-field=name]").fill("prototype")
    upload.locator("[data-field=file]").set_input_files(str(plugin_file))
    upload.locator("[data-field=entrypoint]").fill("prototype:Prototype")
    upload.locator("[data-field=arm]").select_option("on")
    shot("5b-plugins-upload")
    key(page, "pluginsFrame", "UPLD").click()
    expect(status(page, "pluginsFrame")).to_contain_text("Plugin: perception/prototype\nRevision: 1\nCatalog version: 2")
    expect(status(page, "pluginsFrame")).to_contain_text("prototype (revision 1)")
    expect(row(page, "pluginsFrame", "prototype")).to_have_class(re.compile(r"\bmarked\b"))
    row(page, "pluginsFrame", "perception").click()  # back to the host
    expect(status(page, "pluginsFrame")).to_contain_text("- perception/prototype: revision 1 (prototype.py) (armed)")
    # Uploaded plugins cannot be saved.
    key(page, "pluginsFrame", "SAVE").click()
    expect(status(page, "pluginsFrame")).to_contain_text("uploaded plugins cannot be saved (perception/prototype)")
    key(page, "pluginsFrame", "DSALL").click()
    expect(status(page, "pluginsFrame")).to_have_text("Disarm every plugin?")
    expect(mode("pluginsFrame")).to_have_text("chaser · disarm")
    key(page, "pluginsFrame", "YES").click()
    expect(status(page, "pluginsFrame")).to_contain_text("Applied perception: (none)\nApplied memory: (none)\nApplied proposal: (none)")
    clear_of_column(page)
    page.locator("#gapsToggle").click()
    shot("5-plugins-gaps")
    page.locator("#gapsToggle").click()

    key(page, "hostsFrame", "STOP").click()
    expect(status(page, "hostsFrame")).to_contain_text("Stopped the run on host chaser.")
    # Deselecting in Hosts changes nothing elsewhere; Plugins goes back to packaged through HOSTS.
    row(page, "hostsFrame", "chaser").click()
    expect(row(page, "hostsFrame", "chaser")).to_have_class("row")
    expect(page.locator("#pluginsReadout")).to_have_text("chaser")
    verb(page, "pluginsFrame", "HOSTS").click()
    row(page, "pluginsFrame", "packaged").click()
    expect(page.locator("#pluginsReadout")).to_have_text("packaged")
    row(page, "pluginsFrame", "perception").click()
    expect(key(page, "pluginsFrame", "STRM")).to_be_disabled()
    expect(frame(page, "streamFrame1")).to_be_visible()  # a Stream window keeps its own host
    expect(verb(page, "pluginsFrame", "UPLD")).to_be_disabled()  # UPLD needs a host
    shot("6-packaged")

    # simulators delete refuses while a host was created at the simulator.
    button(page, "Simulators").click() if frame(page, "simulatorsFrame").is_hidden() else None
    row(page, "simulatorsFrame", "127.0.0.1:5051").click()
    expect(status(page, "simulatorsFrame")).to_contain_text("  used by host chaser")
    shot("7a-simulator-status")
    # VEC on a tracked simulator selects it in Vehicles and shows its status.
    key(page, "simulatorsFrame", "VEC").click()
    expect(row(page, "vehiclesFrame", "127.0.0.1:5051")).to_have_class(re.compile(r"\bselected\b"))
    expect(status(page, "vehiclesFrame")).to_contain_text("Simulator (chase-sim)  running at http://127.0.0.1:5051/")
    # Untracked, VEC opens the discover form filled in for it; DISC lists it again.
    key(page, "vehiclesFrame", "UNTRK").click()
    key(page, "vehiclesFrame", "YES").click()
    expect(row(page, "vehiclesFrame", "127.0.0.1:5051")).to_have_count(0)
    page.locator("#vehiclesFrame [data-frame-close]").click()
    key(page, "simulatorsFrame", "VEC").click()
    expect(frame(page, "vehiclesFrame")).to_be_visible()
    vform = frame(page, "vehiclesFrame").locator("[data-form=discover]")
    expect(vform).to_be_visible()
    expect(vform.locator("[data-field=provider]")).to_have_value("chase-sim")
    expect(vform.locator("[data-field=url]")).to_have_value("http://127.0.0.1:5051/")
    shot("7b-vec-discover")
    key(page, "vehiclesFrame", "DISC").click()
    expect(status(page, "vehiclesFrame")).to_contain_text("Discovered Simulator (chase-sim) at http://127.0.0.1:5051/")
    expect(row(page, "vehiclesFrame", "127.0.0.1:5051")).to_have_count(1)
    key(page, "simulatorsFrame", "DEL").click()
    expect(status(page, "simulatorsFrame")).to_have_text("Delete?")
    key(page, "simulatorsFrame", "YES").click()
    expect(status(page, "simulatorsFrame")).to_contain_text("Host chaser uses the simulator at http://127.0.0.1:5051/; delete it first.")
    clear_of_column(page)
    shot("7-simulator-delete-blocked")

    # Delete the host, then the simulator: their rows go.
    row(page, "hostsFrame", "chaser").click()
    expect(row(page, "hostsFrame", "chaser")).to_have_class("row selected")
    key(page, "hostsFrame", "DEL").click()
    expect(status(page, "hostsFrame")).to_have_text("Delete?")
    key(page, "hostsFrame", "YES").click()
    expect(row(page, "hostsFrame", "chaser")).to_have_count(0)
    key(page, "simulatorsFrame", "DEL").click()  # still selected after the refusal
    expect(status(page, "simulatorsFrame")).to_have_text("Delete?")
    key(page, "simulatorsFrame", "YES").click()
    expect(row(page, "simulatorsFrame", "127.0.0.1:5051")).to_have_count(0)
    expect(status(page, "simulatorsFrame")).to_have_text("Deleted Simulator (chase-sim) at http://127.0.0.1:5051/")
    expect(page.locator("#simulatorsReadout")).to_have_text("none")
    shot("8-deleted")

    # A simulator whose process died: the pane reads its status, Vehicles drops it, Delete clears the record.
    create_text = verb(page, "simulatorsFrame", "NEW")
    create_text.click()
    expect(row(page, "simulatorsFrame", "127.0.0.1:5051")).to_have_count(1)
    expect(create_text).to_be_enabled()
    row(page, "simulatorsFrame", "127.0.0.1:5051").click()
    expect(key(page, "simulatorsFrame", "DEL")).to_be_visible()
    expect(status(page, "simulatorsFrame")).to_contain_text("Simulator (chase-sim)  running at http://127.0.0.1:5051/")
    page.locator("#endProcess").click()
    expect(status(page, "simulatorsFrame")).to_contain_text("Simulator (chase-sim)  exited at http://127.0.0.1:5051/")
    expect(status(page, "simulatorsFrame")).to_contain_text("Simulator log runtime/simulators/5051/simulator.log:")
    expect(status(page, "simulatorsFrame")).to_contain_text("exiting with code 1")
    expect(row(page, "simulatorsFrame", "127.0.0.1:5051")).to_have_class("row selected exited")
    expect(row(page, "vehiclesFrame", "127.0.0.1:5051")).to_have_count(0)
    expect(page.locator("#vehiclesReadout")).to_have_text("0/0 up")
    shot("8b-exited")
    key(page, "simulatorsFrame", "DEL").click()
    key(page, "simulatorsFrame", "YES").click()
    expect(row(page, "simulatorsFrame", "127.0.0.1:5051")).to_have_count(0)
    expect(page.locator("#simulatorsReadout")).to_have_text("none")

    # Past the window's height the list scrolls; "create" stays put on top.
    create_text = verb(page, "simulatorsFrame", "NEW")
    top = create_text.bounding_box()["y"]
    for n in range(18):
        create_text.click()
        expect(row(page, "simulatorsFrame", f"127.0.0.1:{5051 + n}")).to_have_count(1)
        expect(create_text).to_be_enabled()
    rows_el = frame(page, "simulatorsFrame").locator(".list-rows")
    assert rows_el.evaluate("el => el.scrollHeight > el.clientHeight"), "list should scroll"
    rows_el.evaluate("el => el.scrollTop = el.scrollHeight")
    assert create_text.bounding_box()["y"] == top, "create moved"
    list_level_with_pane(page)
    shot("9-scrolled")

    # Long output scrolls; the pane's clickable text stays put below it.
    sims = frame(page, "simulatorsFrame")
    row(page, "simulatorsFrame", "127.0.0.1:5068").click()
    expect(status(page, "simulatorsFrame")).to_contain_text("running at http://127.0.0.1:5068/")
    page.evaluate("""() => { const W = Simulators.state; W.log = Array.from({length: 40}, (_, i) => `line ${i}`); render(); }""")
    text_box = sims.locator(".float-frame-status-text").bounding_box()
    del_key = key(page, "simulatorsFrame", "DEL")
    expect(del_key).to_be_visible()
    before = del_key.bounding_box()
    assert sims.locator(".float-frame-status-text").evaluate("el => el.scrollHeight > el.clientHeight")
    sims.locator(".float-frame-status-text").evaluate("el => el.scrollTop = 0")
    assert del_key.bounding_box() == before, "the key moved with the output"
    assert del_key.evaluate("el => getComputedStyle(el).textTransform") == "uppercase"
    assert key(page, "simulatorsFrame", "DEL").inner_text() == "DEL"
    pane_box = sims.locator(".float-frame-status").bounding_box()
    del_box = del_key.bounding_box()
    right_gap = pane_box["x"] + pane_box["width"] - (del_box["x"] + del_box["width"])
    bottom_gap = pane_box["y"] + pane_box["height"] - (del_box["y"] + del_box["height"])
    assert abs(right_gap - bottom_gap) <= 0.5, ("gaps", right_gap, bottom_gap)
    plain = del_key.evaluate("el => getComputedStyle(el).backgroundColor")
    assert plain != "rgb(10, 10, 10)", ("still highlighted while the output scrolls", plain)
    assert before["y"] >= text_box["y"] + text_box["height"] - 1, ("key under the output", before, text_box)
    shot("9b-long-output")
    browser.close()

if errors:
    print("page errors:", *errors, sep="\n  ")
    sys.exit(1)
print("ok:", *sorted(path.name for path in SHOTS.glob("*.png")))
