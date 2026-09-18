"""Google Analytics 4 on the documentation site, and the guards around it.

Owner decision 2026-09-17: GA4 on every public site, with the privacy copy changed to match.
This module is the one place that decision lives. :func:`gauntlet.site.page` asks it for
the loader that goes in every page's ``<head>`` and for the footer's privacy line, and
``privacy.html`` says what a build ships because it is rendered from
:data:`GA4_MEASUREMENT_ID` too.

The measurement ID is public, since every page that loads GA hands it to the browser, so it
is configuration rather than a secret. Empty means the site carries no analytics at all: no
``<script>``, no reference to Google, no opt-out control, and a footer and privacy page that
say the site runs none. A malformed ID raises instead of shipping a broken tag.

What the loader does, in order, on every page that carries it:

- Wires the footer's "Opt out of analytics" / "Opt back in" control.
- Returns, loading nothing, unless the page is served from :data:`GA4_HOST` under
  :data:`GA4_PATH`. The same bytes are rendered locally, in CI and for the deploy, so this
  is a run-time guard: ``make site`` output opened from disk, the CI accessibility check,
  or any other copy never loads GA and never sends a hit to the real property.
- Returns when ``navigator.globalPrivacyControl === true``, when Do Not Track is on
  (``navigator.doNotTrack``, ``window.doNotTrack`` or ``navigator.msDoNotTrack`` is "1" or
  "yes"), or when the visitor opted out. No Google script, no ``dataLayer``, no request, and
  no cookie.
- Sets Consent Mode v2 defaults: ``ad_storage``, ``ad_user_data`` and ``ad_personalization``
  denied everywhere, and ``analytics_storage`` denied in the EEA, the UK and Switzerland (via
  ``region``) and granted elsewhere. There is no banner, so nothing updates them: visitors
  in those regions get no GA cookies, and gtag.js sends Google cookieless pings instead.
- Configures gtag with ``allow_google_signals`` and ``allow_ad_personalization_signals``
  both false, then appends gtag.js as an async script. Nothing on the page waits for it.
"""

from __future__ import annotations

import json
import re
from typing import Final

GA4_MEASUREMENT_ID: Final = "G-EHTXRQ49B6"
"""The chelseakr.github.io/gauntlet web stream. Empty ("") means no analytics anywhere."""

GA4_DATA_RETENTION: Final = "14 months"
"""What the privacy page says about retention. It must match the property's Admin > Data
retention setting."""

GA4_HOST: Final = "chelseakr.github.io"
GA4_PATH: Final = "/gauntlet/"
"""Where the loader may run. GitHub Pages serves this project under a path of an origin its
sibling projects share, so the host alone is not enough."""

GA4_OPT_OUT_KEY: Final = "gauntlet:analytics-opt-out"
"""The footer's opt-out, remembered per browser in localStorage.

Every project under chelseakr.github.io shares one origin and so one localStorage. A generic
key would opt a visitor out of every sibling site at once, so this one names the project.
Renaming it would silently opt every opted-out visitor back in: never rename it."""

MEASUREMENT_ID_RE: Final = re.compile(r"G-[A-Z0-9]{4,20}")
"""GA4 web-stream measurement IDs. Checked strictly: the value goes into an inline script."""

EU_MEMBER_STATES: Final = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
)  # fmt: skip
ANALYTICS_DENIED_REGIONS: Final = (*EU_MEMBER_STATES, "IS", "LI", "NO", "GB", "CH")
"""Where ``analytics_storage`` defaults to denied (ISO 3166-1 alpha-2): the EU member states,
the other EEA states (Iceland, Liechtenstein, Norway), the UK and Switzerland."""

GTAG_JS_URL: Final = "https://www.googletagmanager.com/gtag/js"

OPT_OUT_MESSAGES: Final = {
    "__MSG_OPTED_OUT__": (
        "Opted out. From the next page you open, this site will not load Google Analytics "
        "in this browser."
    ),
    "__MSG_IS_OUT__": (
        "You have opted out: this site does not load Google Analytics in this browser."
    ),
    "__MSG_BACK_IN__": "Opted back in. Analytics resumes from the next page you open.",
    "__MSG_SIGNAL__": (
        "Analytics is off: your browser sends Global Privacy Control or Do Not Track."
    ),
    "__MSG_NO_STORAGE__": (
        "This browser is blocking site storage, so an opt-out cannot be remembered here. "
        "Global Privacy Control or Do Not Track keeps analytics off."
    ),
}
"""The footer control's status line after each change, announced by its role="status"."""

_TEMPLATE: Final = r"""<script>
(function () {
  var w = window, n = navigator, d = document, KEY = __OPT_OUT_KEY__, OFF = __GA_DISABLE__;
  var store = null;
  try { store = w.localStorage; store.getItem(KEY); } catch (e) { store = null; }
  function optedOut() {
    try { return !!store && store.getItem(KEY) === "1"; } catch (e) { return false; }
  }
  var dnt = n.doNotTrack || w.doNotTrack || n.msDoNotTrack;
  var signal = n.globalPrivacyControl === true || dnt === "1" || dnt === "yes";
  d.addEventListener("DOMContentLoaded", function () {
    var box = d.querySelector("[data-analytics-choice]");
    if (!box) return;
    var button = box.querySelector("button"), status = box.querySelector("[role=status]");
    function render(message) {
      button.textContent = optedOut() ? "Opt back in" : "Opt out of analytics";
      button.hidden = signal || !store;
      status.textContent = message;
      box.hidden = false;
    }
    button.addEventListener("click", function () {
      try {
        if (optedOut()) {
          store.removeItem(KEY);
          w[OFF] = false;
          render(__MSG_BACK_IN__);
        } else {
          store.setItem(KEY, "1");
          w[OFF] = true;
          render(__MSG_OPTED_OUT__);
        }
      } catch (e) {
        store = null;
        render(__MSG_NO_STORAGE__);
      }
    });
    render(signal ? __MSG_SIGNAL__ : !store ? __MSG_NO_STORAGE__
      : optedOut() ? __MSG_IS_OUT__ : "");
  });
  if (w.location.hostname !== __HOST__) return;
  if (w.location.pathname.indexOf(__PATH__) !== 0) return;
  if (n.globalPrivacyControl === true) return;
  if (dnt === "1" || dnt === "yes") return;
  if (optedOut()) return;
  w.dataLayer = w.dataLayer || [];
  function gtag() { w.dataLayer.push(arguments); }
  gtag("consent", "default", {
    ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    analytics_storage: "denied", region: __DENIED_REGIONS__
  });
  gtag("consent", "default", {
    ad_storage: "denied", ad_user_data: "denied", ad_personalization: "denied",
    analytics_storage: "granted"
  });
  gtag("js", new Date());
  gtag("config", __ID__, {
    allow_google_signals: false, allow_ad_personalization_signals: false
  });
  var s = d.createElement("script");
  s.async = true;
  s.src = __GTAG_SRC__;
  d.head.appendChild(s);
})();
</script>
"""


def measurement_id(value: str | None) -> str | None:
    """None for an unset or blank ID, the ID itself when well formed.

    Anything else raises rather than being written into a script: a typo should fail the
    build, not publish a broken tag.
    """
    if value is None or not value.strip():
        return None
    value = value.strip()
    if not MEASUREMENT_ID_RE.fullmatch(value):
        raise ValueError(f"not a GA4 measurement ID (expected G-XXXXXXXXXX): {value!r}")
    return value


def head_snippet(ga4_id: str | None) -> str:
    """The loader for one page's ``<head>``, or "" when no ID is set."""
    mid = measurement_id(ga4_id)
    if mid is None:
        return ""
    replacements = {
        "__OPT_OUT_KEY__": json.dumps(GA4_OPT_OUT_KEY),
        "__GA_DISABLE__": json.dumps(f"ga-disable-{mid}"),
        "__HOST__": json.dumps(GA4_HOST),
        "__PATH__": json.dumps(GA4_PATH),
        "__DENIED_REGIONS__": json.dumps(list(ANALYTICS_DENIED_REGIONS)),
        "__ID__": json.dumps(mid),
        "__GTAG_SRC__": json.dumps(f"{GTAG_JS_URL}?id={mid}"),
        **{token: json.dumps(message) for token, message in OPT_OUT_MESSAGES.items()},
    }
    snippet = _TEMPLATE
    for token, value in replacements.items():
        snippet = snippet.replace(token, value)
    return snippet


def footer_note(ga4_id: str | None) -> str:
    """The footer's privacy line, true for the build it is in."""
    if measurement_id(ga4_id) is None:
        return (
            '<p class="privacy-note">This site runs no analytics and sets no cookies. '
            '<a href="privacy.html">Privacy</a>.</p>'
        )
    # "Google Analytics" without the "4": the footer is prose on every page, and
    # tests/test_site.py holds every number in prose to a count or a reviewed reason.
    return (
        '<p class="privacy-note">This site counts visits with Google Analytics, advertising '
        "features off, and does not load it when your browser sends Global Privacy Control or "
        'Do Not Track. <a href="privacy.html">Privacy</a>.\n'
        '<span data-analytics-choice hidden><button type="button" class="link-button">'
        'Opt out of analytics</button> <span role="status"></span></span></p>'
    )
