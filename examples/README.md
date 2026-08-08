# Examples

What a repository that is not this one would write.

- [`cases/grounding.yaml`](cases/grounding.yaml): a minimal external case file.
  One gate per file, English and Spanish cases as peers.
- [`broken_target.py`](broken_target.py): the smallest thing `--callable`
  accepts, a factory returning an object with a `name` and an
  `ask(prompt, language)` method. `make_target` is broken on purpose;
  `make_healthy_target` is not.

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

The module is importable as `broken_target` because `gauntlet run --callable`
puts the working directory on the import path. Run these commands from
`examples/`, or point `--callable` at a module your own project already exposes.

CI runs the GitHub Action against these files, so the failure path of the action
is exercised rather than assumed.
