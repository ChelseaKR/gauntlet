---
name: Report a screen-reader or keyboard session
about: You walked one of the published pages with a screen reader, a keyboard, or at 320px
title: "[a11y session] <page> - <your screen reader + browser>"
labels: ["accessibility", "help wanted"]
---

<!--
The README already says where the automated floor stops:

  "What still needs a person: none of this looks at the pages. Layout, reflow
  at small widths, focus visibility in practice, and reading order under a real
  screen reader are not settled by any check here."

That is honest and it is also a gap that has stayed open (issue #35). Contrast
is measured as arithmetic over both palettes, structure is checked in two
toolchains, and rules jsdom cannot run are reported as not-run rather than as
passed. None of that listens to a page.

You do not need to be an accessibility expert. You need a screen reader you
already use, or a keyboard.

ONE PAGE IS A WHOLE CONTRIBUTION. There are five published pages
(https://chelseakr.github.io/gauntlet/): index, gates, california, action,
evidence. They are documentation pages, not an application: no forms, no
scripts to drive, one long table on the gates page. Roughly 10 to 20 minutes
per page, more for gates because of the table. Walking one and stopping is
fine and expected.
-->

## What you walked

- **Page (paste the URL):**
- **How long it took you:** <!-- honestly -->

## Your setup

A screen reader and a browser fail as a pair, not separately, so both versions
matter.

- **Screen reader + version:** <!-- e.g. VoiceOver on macOS 15.3, NVDA 2024.4 -->
- **Browser + version:**
- **Operating system + version:**
- **Keyboard only, or screen reader, or both:**
- **If you tested reflow:** viewport or zoom level you used

## What happened

<!--
Describe what you did and what you heard or saw, in order. Quote what was
announced where you can, including the parts that were wrong.

Please do NOT tell us whether the page conforms to anything. That is not what
this report is for, and a report that leads with "looks fine" is a report that
gets nodded through. Say what happened.

The gates page is the one most likely to have a real problem: it is a long
table of gate names and what each enforces, and a table read linearly by voice
is where reading order and header association usually break.
-->

## Where it stopped, or got hard

- [ ] I got through the page
- [ ] I got through it, but it was harder than it should have been
- [ ] I could not get through it
- [ ] I could not tell whether it worked

<!--
"I could not tell" is a real answer and it is wanted as itself. This project's
own doctrine is that a rule that cannot run is reported as not-run rather than
as passed; the same applies to a check you could not actually make.
-->

## How you want to be credited

- **Name, handle, or organisation to record:**
- [ ] Record me by name
- [ ] Record a handle or an organisation instead
- [ ] Do not record me

<!-- Nobody will push you to be named. See docs/HELP-WANTED.md for the open
question about what a credit line should be allowed to say. -->

## Did you walk another project in the same sitting?

A session report is portable. Several projects in this portfolio are each
blocked on a manual screen-reader and keyboard pass and none has ever had one:

- gauntlet (this repo), issue #35
- homeroom #6
- tods-validate #74 and #184
- ctdl-validate #54
- fare-policy-assistant #201
- permit-bearings, the manual rows in `docs/MANUAL-VALIDATION.md`

File the detail wherever you did the most, and link that issue from the others
rather than retyping it.

- **Other session reports:**
