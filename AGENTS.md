# AGENTS.md — agent guide for tuna-os/changelog-action

A **GitHub Action** that generates a changelog between two container image
versions by diffing their RPM lists with `skopeo` and verifying attestation
with `cosign`. It supports explicit tag comparison and automatic tag discovery
from a release stream.

Human docs: [`README.md`](README.md) (usage, the full input table),
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## The interface is `action.yml`, and consumers pin a moving branch

`action.yml`'s `inputs:` block is this repo's **public API** — `family`,
`registry`, `cosign-key`, `images`, `stream`, `tag-pattern`, `prev_tag`,
`curr_tag`, `handwritten`, `output`, `output-env`. Renaming one, or changing
what a default means, breaks every workflow that calls it.

Two facts make that sharper than usual:

- **The default branch is `master`**, not `main`. Branch from it, target it.
- **There are no release tags**, so callers pin `@master` (the README says so).
  Every consumer therefore tracks this branch's tip: a change is live for them
  the moment it merges, with no version to hold them back and no way to roll
  back except another commit. Cutting a `v1` tag would fix that; until then,
  treat every merge here as an immediate production change.

## Layout and checks

| Path | What |
|---|---|
| `action.yml` | the input contract |
| `changelog.py` | the implementation (~750 lines) |
| `test_changelog.py` | the suite — 42 tests |
| `Dockerfile` | the action's runtime |

```bash
python3 -m pytest test_changelog.py -q   # 42 passed
ruff check .                             # config in ruff.toml
```

> **`ruff` is configured but not enforced.** `ruff.toml` sets the rules and
> `test.yml` runs *only* pytest, so nothing checks them and the tree has
> drifted: `ruff check .` reports 18 findings on `master`.
>
> They are style, not defects — I checked the two that can indicate real bugs.
> `F811` is a redundant `import sys` inside `main()` shadowing the module-level
> import, and `F841` is an unused `cmd` variable in a test. Neither changes
> behaviour. The rest are import ordering, unused imports and long lines.

## External tools it shells out to

`skopeo` (inspect image manifests and RPM lists) and `cosign` (verify the
attestation). Both are external processes, so their output format is an
implicit dependency: a change in either tool's CLI output can break parsing
without anything in this repo changing. The tests stub these rather than
hitting a registry, which is what makes the suite fast and offline — keep new
tests that way.

## When changing tag discovery

`stream` and `tag-pattern` drive auto-discovery (find the two most recent
`{stream}-YYYYMMDD` tags); `prev_tag`/`curr_tag` are the explicit path and are
**ignored when `stream` is set**. That precedence is easy to invert by
accident, and a workflow that silently compares the wrong pair produces a
changelog that looks plausible and is wrong — which is worse than an error.
