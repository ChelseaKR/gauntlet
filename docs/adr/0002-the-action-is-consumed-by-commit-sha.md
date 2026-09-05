# ADR 0002: The action is consumed by commit SHA, and no movable tag is published

**Status:** Accepted

**Date:** 2026-09-05

**Decider:** Chelsea Kelly-Reif (owner direction); drafted by the agent that
implemented it

## Context

[SCOPE.md](../../SCOPE.md) has carried an open question for the owner since the
repository went public: whether the GitHub Action should be referenceable by tag
rather than by commit SHA. The package half of that question is settled.
`v0.1.0` is tagged, and `gauntlet-evals` 0.1.0 is on PyPI. The action half was
never decided, and the silence itself was the problem: README.md,
[src/gauntlet/site.py](../../src/gauntlet/site.py) and the composite action's own
header all tell a consumer to pin a SHA, without anywhere recording that this was
a choice or what it costs.

Four facts about the current state, checked rather than assumed:

1. `action.yml` sits at the repository root and is a composite action, so
   `uses: ChelseaKR/gauntlet@<ref>` is the only distribution channel it has.
   There is no package to install and no registry entry.
2. `v0.1.0` is an annotated tag whose tree contains `action.yml`
   (`git show v0.1.0:action.yml`), so `uses: ChelseaKR/gauntlet@v0.1.0` already
   resolves today. "Referenceable by tag" is therefore not a thing that has to be
   built; it is a thing that already works and is undocumented.
3. What does not exist is a movable major tag, the `@v1` form that
   `actions/checkout@v5` and the rest of the ecosystem's convention means. That
   form is a reference whose target is changed by the publisher after consumers
   have adopted it.
4. This repository pins every action it consumes to a full 40-character commit
   SHA, and two tests fail the build if any reference drops to anything shorter:
   `test_action_pins_every_dependency_to_a_commit_sha` and
   `test_workflow_pins_every_action_to_a_commit_sha` in
   `tests/test_docs_and_inventory.py`. Dependabot's `github-actions` ecosystem is
   configured with a 7 day cooldown and keeps those SHA pins current.

The trade is real in both directions, and neither side is a technicality.

**For the SHA.** A git tag is a mutable ref. `@v1` means "whatever the publisher
has pointed v1 at since you last looked", and a runner resolves it at job start,
so code that nobody in the consuming repository reviewed can execute inside a job
that has their checkout and their permissions. This is the supply-chain hazard
that pinning exists to close, and it is the reason this repository pins every
action it uses.

**For the tag.** A movable tag is what makes an action usable by other people.
It is the form every published example shows, the form a reader recognizes, and
the form that lets a consumer take a fix without editing a hex string. An action
whose only documented reference is a 40 character SHA has adoption friction that
a SHA pin does not remove from anyone's supply chain, because the consumer who
finds that friction annoying is the consumer who copies `@main`.

## Decision

1. **The documented and recommended way to consume this action stays a full
   commit SHA**, with the version in a trailing comment, which is the form this
   repository requires of every action it consumes itself. A gate harness whose
   whole argument is that an assurance is worth what its inputs are pinned to
   cannot tell its own consumers to accept a reference that can change meaning
   after review.

2. **Immutable version tags are supported and documented as the second form.**
   `uses: ChelseaKR/gauntlet@v0.1.0` resolves today. Tags in this repository name
   a release and are never moved once pushed, so a version tag is immutable in
   practice, and the documentation now says so instead of leaving a reader to
   guess that only a SHA works.

3. **No movable major tag will be published.** There will be no `@v0` or `@v1`
   that is repointed at later commits. It is the one form that would make the
   reference mean something different tomorrow than it meant when a reviewer
   approved it, and repointing it is a force update of a published ref, which
   this project does not do to anything it has published.

4. **No GitHub Marketplace listing is made by this decision.** A listing is
   possible without any new tag, because a published release already exists, but
   it is a separate decision about distribution and outreach and is not made
   here.

This ADR creates no tag. Point 2 records that an existing tag resolves; it does
not authorize a new one.

## Consequences

- A consumer who wants the ecosystem convention does not get it. `@v1` will not
  exist here, and someone who insists on a floating major reference will pin a
  branch instead, which is worse and which this project cannot prevent.
- Taking an update is an edit to a SHA. For a consuming repository with
  Dependabot or Renovate configured for `github-actions` that edit arrives as a
  pull request, the same way this repository receives its own; for a repository
  with neither, it is manual.
- The documentation stops being silently inconsistent. README.md and the
  generated action page said "pin to a commit SHA" and "no release tag is
  implied" while a usable release tag existed, which reads as though the tag form
  does not work rather than as though it is not the recommended one.
- SCOPE.md's open question 4 closes, and the Release & Versioning row in the
  README's standards table points at this file instead of stating the SHA pin as
  a bare fact.
- If the action's interface stabilizes and the absence of a floating major turns
  out to be the thing keeping adopters away, that is the evidence that would
  reopen this. Reopening means a new ADR that supersedes this one and states
  what changed, not an edit here.
