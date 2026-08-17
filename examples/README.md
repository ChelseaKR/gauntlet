# Examples

What a repository that is not this one would write.

- [`cases/grounding.yaml`](cases/grounding.yaml): a minimal external case file.
  One gate per file, English and Spanish cases as peers.
- [`cases-absence-only/adversarial.yaml`](cases-absence-only/adversarial.yaml):
  a case set whose every check is phrased as the absence of something bad, which
  is the shape a target can pass by saying nothing.
- [`broken_target.py`](broken_target.py): the smallest thing `--callable`
  accepts, a factory returning an object with a `name` and an
  `ask(prompt, language)` method. `make_target` is broken on purpose;
  `make_healthy_target` is not; `make_mute_target` answers nothing at all.

Run both, and compare the runs:

```sh
uv run gauntlet run --cases examples/cases \
  --callable broken_target:make_healthy_target --out healthy.json

uv run gauntlet run --cases examples/cases \
  --callable broken_target:make_target --out broken.json

uv run gauntlet report broken.json --baseline healthy.json --out evidence.md
```

The first run passes, the second fails every grounded case with "uncited
answer", and the evidence pack reports the whole-run drift between them.

And the run the harness refuses to score:

```sh
uv run gauntlet run --cases cases-absence-only \
  --callable broken_target:make_mute_target
```

Every check in `cases-absence-only` is satisfied by silence, and this target is
silent, so there is nothing in the run that could have caught it. The harness
prints `overall: UNSCOREABLE` and exits 4 instead of reporting 4 of 4 and a pass.
Swap in `make_healthy_target` and the same directory is scored normally: the
refusal is aimed at silence, not at absence-phrased suites as such.

The module is importable as `broken_target` because `gauntlet run --callable`
puts the working directory on the import path. Run these commands from
`examples/`, or point `--callable` at a module your own project already exposes.

CI runs the GitHub Action against these files, so both the failure path and the
unscoreable path of the action are exercised rather than assumed.
