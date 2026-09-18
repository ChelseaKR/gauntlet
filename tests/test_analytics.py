"""Google Analytics 4 on the documentation site, and the guards around it.

Owner decision 2026-09-17: GA4 on every public site, with the privacy copy changed to match.
:mod:`gauntlet.analytics` owns the loader, and this module holds it to three things.

**The build.** No ID means no analytics on any page: no script, no reference to Google, no
opt-out control, and copy that says so. A malformed ID fails the build. With the committed
ID, every page carries exactly one loader, the footer control and a link to the privacy
page.

**The loader, executed.** The script is lifted out of a page the real build writes and run
in Node against a stubbed ``window``, ``navigator``, ``document`` and ``localStorage``. Off
the production host or outside this project's path, under Global Privacy Control, under any
of the Do Not Track spellings, or after the footer opt-out, it creates no ``dataLayer`` and
requests nothing. Otherwise it sets both Consent Mode defaults before ``config``, turns
Google signals and ad personalization off, and appends gtag.js once.

**Negative controls.** Each guard is deleted from the script in turn. Every control first
asserts that the deletion landed (the guard occurred exactly once before and not at all
after), then asserts that GA now loads in the case the guard exists for. A control whose
sabotage silently no-ops would otherwise read as a pass.

Node is on every GitHub-hosted runner. Locally the executed tests skip when it is missing;
under CI they fail instead, so a runner without Node cannot turn them into a green skip.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Final

import pytest

from gauntlet import analytics
from gauntlet.site import PAGES, build_site, load_action, render_site

ROOT: Final = Path(__file__).resolve().parents[1]
ACTION: Final = ROOT / "action.yml"
PAGE_NAMES: Final = tuple(name for name, _label, _key in PAGES)

ID: Final = "G-EHTXRQ49B6"
KEY: Final = "gauntlet:analytics-opt-out"
GTAG_SRC: Final = f"https://www.googletagmanager.com/gtag/js?id={ID}"
DENIED_REGIONS: Final = [
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO", "GB", "CH",
]  # fmt: skip

PRODUCTION: Final = {"hostname": "chelseakr.github.io", "pathname": "/gauntlet/"}

GUARDS: Final = {
    "host": 'if (w.location.hostname !== "chelseakr.github.io") return;',
    "path": 'if (w.location.pathname.indexOf("/gauntlet/") !== 0) return;',
    "gpc": "if (n.globalPrivacyControl === true) return;",
    "dnt": 'if (dnt === "1" || dnt === "yes") return;',
    "opt-out": "if (optedOut()) return;",
}
"""The guards, exactly as the rendered script spells them. The negative controls delete
each one, and the occurrence count is how they prove the deletion landed."""

# A stand-in for the browser: just enough window, navigator, document and localStorage
# for the loader to run, recording what it pushed and what it appended, and driving the
# footer control through DOMContentLoaded and clicks.
HARNESS: Final = r"""
const vm = require("vm");
const fs = require("fs");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const out = {};
for (const sc of input.scenarios) {
  const appended = [];
  const listeners = {};
  const store = Object.assign({}, sc.storage || {});
  const status = { textContent: "" };
  const button = {
    hidden: false, textContent: "Opt out of analytics", handlers: {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
  };
  const box = {
    hidden: true,
    querySelector(sel) {
      if (sel === "button") return button;
      return sel === "[role=status]" ? status : null;
    },
  };
  const document = {
    head: { appendChild(el) { appended.push(el); } },
    createElement(tag) { return { tag: tag }; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    querySelector(sel) { return sel === "[data-analytics-choice]" ? box : null; },
  };
  const window = {
    location: { hostname: sc.hostname, pathname: sc.pathname },
    doNotTrack: sc.windowDnt,
  };
  if (sc.storageThrows) {
    Object.defineProperty(window, "localStorage", {
      get() { throw new Error("SecurityError"); },
    });
  } else {
    window.localStorage = {
      getItem(k) { return has(store, k) ? store[k] : null; },
      setItem(k, v) { store[k] = String(v); },
      removeItem(k) { delete store[k]; },
    };
  }
  const navigator = {
    globalPrivacyControl: sc.gpc, doNotTrack: sc.dnt, msDoNotTrack: sc.msDnt,
  };
  const context = vm.createContext({
    window: window, navigator: navigator, document: document, Date: Date,
  });
  vm.runInContext(input.script, context);
  (listeners.DOMContentLoaded || []).forEach((fn) => fn());
  const control = [{
    label: button.textContent, hidden: button.hidden, boxHidden: box.hidden,
    status: status.textContent,
  }];
  for (let i = 0; i < (sc.clicks || 0); i++) {
    button.handlers.click();
    control.push({
      label: button.textContent, hidden: button.hidden, status: status.textContent,
      flag: has(store, input.key) ? store[input.key] : null,
      disabled: window["ga-disable-" + input.id],
    });
  }
  out[sc.name] = {
    dataLayer: window.dataLayer === undefined ? null : window.dataLayer.map(
      (args) => Array.from(args).map((a) => (a instanceof Date ? "<date>" : a)),
    ),
    scripts: appended.map((el) => ({ tag: el.tag, src: el.src, async: el.async })),
    control: control,
  };
}
process.stdout.write(JSON.stringify(out));
"""

Scenario = dict[str, Any]
Result = dict[str, Any]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The site as ``gauntlet site`` writes it for the deploy."""
    out = tmp_path_factory.mktemp("site")
    build_site(out, action_file=ACTION)
    return out


def loader(page: Path) -> str:
    """The inline script in a built page's ``<head>``, exactly as it would be served."""
    head = page.read_text(encoding="utf-8").split("</head>", 1)[0]
    scripts = re.findall(r"<script>(.*?)</script>", head, re.DOTALL)
    assert len(scripts) == 1, f"{page.name}: expected one inline script, found {len(scripts)}"
    return str(scripts[0])


def run(script: str, scenarios: list[Scenario], tmp_path: Path) -> dict[str, Result]:
    """Execute the loader once per scenario and report what it did."""
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("Node is required in CI to execute the GA4 loader")
        pytest.skip("node is not installed; CI runs these")
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    payload = json.dumps({"script": script, "scenarios": scenarios, "key": KEY, "id": ID})
    done = subprocess.run(  # noqa: S603 -- fixed argv, no shell, input is this test's own JSON
        [node, str(harness)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, f"node harness failed: {done.stderr}"
    results: dict[str, Result] = json.loads(done.stdout)
    assert set(results) == {scenario["name"] for scenario in scenarios}
    return results


def scenario(name: str, **overrides: object) -> Scenario:
    return {"name": name, **PRODUCTION, **overrides}


def loads_ga(result: Result) -> bool:
    return result["dataLayer"] is not None or bool(result["scripts"])


# --- the build ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_no_id_means_no_analytics_on_any_page(empty: str | None) -> None:
    pages = render_site(load_action(ACTION), ga4_id=empty)
    assert set(pages) == set(PAGE_NAMES)
    for name, page in pages.items():
        assert "<script" not in page, name
        assert "googletagmanager" not in page, name
        assert "Google Analytics" not in page, name
        assert "Opt out of analytics" not in page, name
        assert "data-analytics-choice" not in page, name
        assert "This site runs no analytics" in page, name


def test_a_malformed_id_fails_the_build() -> None:
    for bad in ("UA-12345-1", "G-abc123", 'G-ABC"};alert(1);//', "G-", "EHTXRQ49B6"):
        with pytest.raises(ValueError, match="not a GA4 measurement ID"):
            analytics.head_snippet(bad)
        with pytest.raises(ValueError, match="not a GA4 measurement ID"):
            render_site(load_action(ACTION), ga4_id=bad)
    assert analytics.measurement_id(f"  {ID} ") == ID


def test_every_page_carries_one_loader_with_the_committed_id(built: Path) -> None:
    assert analytics.GA4_MEASUREMENT_ID == ID
    for name in PAGE_NAMES:
        source = (built / name).read_text(encoding="utf-8")
        assert source.count("<script") == 1, name
        assert "<script" not in source.split("</head>", 1)[1], name
        script = loader(built / name)
        assert json.dumps(ID) in script
        assert json.dumps(GTAG_SRC) in script
        assert json.dumps(KEY) in script
        for guard in GUARDS.values():
            assert script.count(guard) == 1, (name, guard)


def test_every_page_links_the_privacy_page_and_the_opt_out(built: Path) -> None:
    for name in PAGE_NAMES:
        footer = (built / name).read_text(encoding="utf-8").split('<footer class="site">', 1)[1]
        assert '<a href="privacy.html">Privacy</a>' in footer, name
        assert "Google Analytics" in footer, name
        assert (
            '<span data-analytics-choice hidden><button type="button" class="link-button">'
            'Opt out of analytics</button> <span role="status"></span></span>'
        ) in footer, name


@pytest.mark.parametrize(
    "claim",
    [
        "Google Analytics 4",
        "Google LLC",
        "<code>_ga</code>",
        "European\nEconomic Area, the United Kingdom and Switzerland",
        "cookieless ping",
        "Google signals and ad personalization are both turned off",
        "14 months",
        "Global Privacy Control",
        "Do Not Track",
        "&ldquo;Opt out of analytics&rdquo;",
        "&ldquo;Opt back in&rdquo;",
        f"<code>{KEY}</code>",
        "https://tools.google.com/dlpage/gaoptout",
    ],
)
def test_the_privacy_page_describes_what_ships(built: Path, claim: str) -> None:
    text = (built / "privacy.html").read_text(encoding="utf-8")
    assert claim in text
    assert "runs no analytics" not in text
    assert analytics.GA4_DATA_RETENTION == "14 months"


def test_the_opt_out_key_names_this_project() -> None:
    """Every chelseakr.github.io project shares one localStorage, so the key names this one."""
    assert analytics.GA4_OPT_OUT_KEY == KEY
    assert KEY.startswith("gauntlet:")


# --- the loader, executed ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["index.html", "privacy.html"])
def test_on_the_production_page_it_loads_with_the_right_config(
    built: Path, name: str, tmp_path: Path
) -> None:
    result = run(loader(built / name), [scenario("live")], tmp_path)["live"]
    assert result["scripts"] == [{"tag": "script", "src": GTAG_SRC, "async": True}]
    ads_denied = {
        "ad_storage": "denied",
        "ad_user_data": "denied",
        "ad_personalization": "denied",
    }
    assert result["dataLayer"] == [
        [
            "consent",
            "default",
            {**ads_denied, "analytics_storage": "denied", "region": DENIED_REGIONS},
        ],
        ["consent", "default", {**ads_denied, "analytics_storage": "granted"}],
        ["js", "<date>"],
        ["config", ID, {"allow_google_signals": False, "allow_ad_personalization_signals": False}],
    ]
    assert len(DENIED_REGIONS) == 32


def test_off_the_production_host_or_path_it_loads_nothing(built: Path, tmp_path: Path) -> None:
    cases = [
        scenario("localhost", hostname="localhost"),
        scenario("loopback", hostname="127.0.0.1"),
        scenario("file", hostname="", pathname="/Users/x/site/index.html"),
        scenario("other-host", hostname="example.com"),
        scenario("sibling-project", pathname="/cairn/"),
        scenario("origin-root", pathname="/"),
        scenario("prefix-lookalike", pathname="/gauntlet-fork/"),
    ]
    for name, result in run(loader(built / "index.html"), cases, tmp_path).items():
        assert not loads_ga(result), (name, result)


def test_a_privacy_signal_or_the_opt_out_loads_nothing(built: Path, tmp_path: Path) -> None:
    cases = [
        scenario("gpc", gpc=True),
        scenario("dnt-navigator", dnt="1"),
        scenario("dnt-yes", dnt="yes"),
        scenario("dnt-window", windowDnt="1"),
        scenario("dnt-ms", msDnt="1"),
        scenario("opted-out", storage={KEY: "1"}),
    ]
    for name, result in run(loader(built / "index.html"), cases, tmp_path).items():
        assert not loads_ga(result), (name, result)


def test_an_unrelated_or_false_signal_still_loads(built: Path, tmp_path: Path) -> None:
    """GPC false, DNT "0", or a sibling site's opt-out key do not switch this one off."""
    cases = [
        scenario("gpc-false", gpc=False),
        scenario("dnt-zero", dnt="0"),
        scenario("sibling-opt-out", storage={"cairn:analytics-opt-out": "1"}),
        scenario("opt-out-not-1", storage={KEY: "0"}),
        scenario("storage-blocked", storageThrows=True),
        scenario("deep-path", pathname="/gauntlet/gates.html"),
    ]
    for name, result in run(loader(built / "index.html"), cases, tmp_path).items():
        assert loads_ga(result), (name, result)


def test_the_footer_control_opts_out_and_back_in(built: Path, tmp_path: Path) -> None:
    results = run(loader(built / "index.html"), [scenario("toggle", clicks=2)], tmp_path)
    control = results["toggle"]["control"]
    assert control[0]["label"] == "Opt out of analytics"
    assert control[0]["hidden"] is False
    assert control[0]["boxHidden"] is False
    assert control[1]["label"] == "Opt back in"
    assert control[1]["flag"] == "1"
    assert control[1]["disabled"] is True
    assert "Opted out" in control[1]["status"]
    assert control[2]["label"] == "Opt out of analytics"
    assert control[2]["flag"] is None
    assert control[2]["disabled"] is False
    assert "Opted back in" in control[2]["status"]


def test_the_footer_control_reports_a_signal_or_an_earlier_opt_out(
    built: Path, tmp_path: Path
) -> None:
    results = run(
        loader(built / "index.html"),
        [
            scenario("gpc", gpc=True),
            scenario("was-out", storage={KEY: "1"}),
            scenario("no-storage", storageThrows=True),
        ],
        tmp_path,
    )
    assert results["gpc"]["control"][0]["hidden"] is True
    assert "Global Privacy Control" in results["gpc"]["control"][0]["status"]
    assert results["was-out"]["control"][0]["label"] == "Opt back in"
    assert "You have opted out" in results["was-out"]["control"][0]["status"]
    assert results["no-storage"]["control"][0]["hidden"] is True
    assert "blocking site storage" in results["no-storage"]["control"][0]["status"]


# --- negative controls ---------------------------------------------------------------------

TRIGGERS: Final = {
    "host": scenario("host", hostname="localhost"),
    "path": scenario("path", pathname="/cairn/"),
    "gpc": scenario("gpc", gpc=True),
    "dnt": scenario("dnt", dnt="1"),
    "opt-out": scenario("opt-out", storage={KEY: "1"}),
}


def test_every_guard_has_a_control() -> None:
    assert set(TRIGGERS) == set(GUARDS)


def test_the_intact_script_holds_every_trigger(built: Path, tmp_path: Path) -> None:
    results = run(loader(built / "index.html"), list(TRIGGERS.values()), tmp_path)
    for name, result in results.items():
        assert not loads_ga(result), f"the intact script loaded GA for {name}"


@pytest.mark.parametrize("guard", sorted(GUARDS))
def test_each_guard_is_what_stops_ga(built: Path, guard: str, tmp_path: Path) -> None:
    original = loader(built / "index.html")
    text = GUARDS[guard]
    assert original.count(text) == 1, "the guard is not in the script"
    sabotaged = original.replace(text, "")
    # The sabotage landed: the guard is gone and nothing else moved.
    assert sabotaged.count(text) == 0
    assert len(original) - len(sabotaged) == len(text)
    result = run(sabotaged, [TRIGGERS[guard]], tmp_path)[guard]
    assert loads_ga(result), f"removing the {guard} guard changed nothing"
