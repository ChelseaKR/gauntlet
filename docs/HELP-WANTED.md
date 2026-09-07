# Help wanted: the two things here a person has to do

Gauntlet runs YAML-driven gate suites against a generative AI feature, fails the
build when a gate fails, and emits the run as a machine-diffable JSON pack and a
document a reviewer can attach to a risk assessment. It evaluates a deployed
feature in its context, not a foundation model, and it depends on no model
vendor.

Two things in it cannot be settled by running it. Both have been open a while,
and both are named in the repository's own prose rather than hidden.

## 1. Nobody has looked at the published pages

Issue [#35](https://github.com/ChelseaKR/gauntlet/issues/35).

The README says where the automated floor stops, and it is not being modest:

> **What still needs a person:** none of this looks at the pages. Layout,
> reflow at small widths, focus visibility in practice, and reading order under
> a real screen reader are not settled by any check here.

The floor underneath that sentence is genuine. Contrast is measured as
arithmetic over both palettes in `tests/test_site.py`. Page structure is checked
in both toolchains, so `make verify` keeps a floor when Node is unavailable.
Rules jsdom cannot run, `target-size` and contrast, are reported as **not-run**
rather than as passed, which is the honest thing to do and is the reason this
gap is visible at all.

None of it listens to a page. The site is a build artifact published from `main`
by `pages.yml`, and no human has read it with a screen reader.

**What one hour buys.** There are five published pages at
<https://chelseakr.github.io/gauntlet/>: index, gates, california, action,
evidence. They are documentation pages, not an application: no forms, nothing
to drive, one long table. Roughly 10 to 20 minutes each, more for the gates
page because a long table read linearly by voice is where reading order and
header association usually break. **One page is a whole contribution.** Walking
one and stopping is expected, not a partial job.

Report it with the [session
template](https://github.com/ChelseaKR/gauntlet/issues/new?template=screen-reader-session.md).

## 2. Nobody on the procurement side has read the mapping table

Issue [#34](https://github.com/ChelseaKR/gauntlet/issues/34), which is open
question 3 in [`SCOPE.md`](../SCOPE.md). The note there records how long it has
been open: *"Asked before the repo went public, and still open now that it is."*

The mapping table in [`docs/california-mapping.md`](california-mapping.md)
claims a correspondence between what these gates find and how California's
published GenAI risk and procurement framework reads. That is the one claim in
this repository an engineer cannot self-certify, because it is a claim about
how other people read a document.

**What one hour buys.** A read of the rows you actually know something about,
from someone who has read or written procurement language for public-sector
software. Not the whole table. **One row is a whole contribution**, and the
single most valuable finding would be a row that reads as claiming more than
"aligned to" - because the language in this project is "aligned to", never
"approved by", and a row that drifts from that is a defect.

Report it with the [procurement read
template](https://github.com/ChelseaKR/gauntlet/issues/new?template=procurement-read.md).

## What this project does not claim, and will not

The State of California, the California Department of Technology, and the
Department of General Services have not reviewed, approved, endorsed, or
certified this project or anything it emits. Reviewing the mapping does not
change that and is not being asked to. Neither task here is an endorsement of
anything, and neither will be described as one.

## What you get

- **Your name, handle, or organisation recorded** against the read or the
  session, at your choice, in a committed file in a public repository. Both
  templates offer an anonymous option and nobody will push you off it.
- **A dated, citable artifact.** This repository has a `CITATION.cff`. "I
  performed the screen-reader pass on these pages on this date", or "I read the
  procurement mapping and here is what I said", is a thing you can link to. Both
  kinds of work usually disappear into private audit documents; these do not.
- **A specific answer, not a thank-you note.** A finding becomes an issue with
  your report linked from it.

## The question this raises rather than settles

Credit means being named in a public file, and for some people that is not
free. Neither template forces it, and neither invents a policy about what a
credit line may say instead of a legal name. That is the maintainer's call.

It is worth knowing that sibling projects in this portfolio have answered it in
opposite directions: contextsafe's hazard register accepts pseudonymity and
publishes no roster without individual written consent, while
trans-docs-navigator requires a named verifier on a public roster and serves an
audience for whom being named carries real risk. Those cannot both be the right
default everywhere, and it should not be settled in an issue thread.

## Reusing one session across several projects

This is not the only project here blocked on a manual screen-reader pass. One
sitting answers the same question for several, and the report is portable:

- gauntlet, [#35](https://github.com/ChelseaKR/gauntlet/issues/35)
- homeroom, [#6](https://github.com/ChelseaKR/homeroom/issues/6)
- tods-validate, [#74](https://github.com/ChelseaKR/tods-validate/issues/74)
  and [#184](https://github.com/ChelseaKR/tods-validate/issues/184)
- ctdl-validate, [#54](https://github.com/ChelseaKR/ctdl-validate/issues/54)
- fare-policy-assistant,
  [#201](https://github.com/ChelseaKR/fare-policy-assistant/issues/201)
- permit-bearings, the manual rows in its `docs/MANUAL-VALIDATION.md`

File the detail wherever you did the most work and link that issue from the
others. Nobody should have to type a session report twice.
