# ADR 0000: Record architecture decisions

**Status:** Accepted

**Date:** 2026-08-17

**Decider:** Chelsea Kelly-Reif

## Context

Gauntlet's load-bearing decisions are currently argued in prose spread across
[README.md](../../README.md), [SCOPE.md](../../SCOPE.md),
[CONTRIBUTING.md](../../CONTRIBUTING.md), [SECURITY.md](../../SECURITY.md) and
[docs/california-mapping.md](../california-mapping.md). That prose says what the
harness does and refuses to do, but it does not say when each rule was adopted,
what was considered instead, or what would have to change for it to be revisited.
A reader who disagrees with a rule has nowhere to look for the argument, and a
future change can quietly reverse one without anything recording that a reversal
happened.

This matters more here than in most repositories. A gate harness is only worth
something if its refusals are stable: "silence is not a pass" and "aligned to,
never approved by" are claims about what the tool will keep doing, and they are
worth no more than the record that holds them in place.

## Decision

Record architecture decisions as Markdown files in this directory, numbered
sequentially as `NNNN-title.md`, in the MADR-ish shape this file uses: status,
date, decider, context, decision, consequences.

This file is number 0000 and the entry point. It records no design decision of
its own beyond the decision to keep the log.

A decision belongs here when reversing it would change what a run means, what
the evidence pack claims, or what the project refuses to do. Routine
implementation choices do not.

Superseding a decision creates a new ADR that links back to the old one, and the
old record keeps its text. Accepted history is not rewritten to make the current
design look inevitable.

No decision is backfilled. The existing prose stays where it is and remains the
description of current behaviour; ADRs start from here and cover decisions made
from this point on. A backfilled ADR would be a reconstruction, and this log is
worth less if some of its entries are reconstructions that read like records.

## Consequences

- The standard discovery path resolves, for a reader and for tooling that looks
  for it.
- New load-bearing decisions carry their reasoning and their date.
- The log starts nearly empty and stays that way until there is a real decision
  to record. That is the honest state, not a gap to fill.
- Decisions predating this file remain documented only as current behaviour in
  the prose above, without a dated record of the alternatives.
