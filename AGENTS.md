# AGENTS.md — agent guide for tuna-os/changelog-action

A **GitHub Action** that generates a changelog between two container image
versions by diffing their RPM lists with `skopeo` and verifying attestation
with `cosign`. It supports explicit tag comparison and automatic tag discovery
from a release stream.

Human docs: [`README.md`](README.md) (usage, the full input table),
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## The interface is `action.yml`, and consumers pin a moving branch

`action.yml`'s `inputs:` block is this repo's **public API** — `family`,
`registry`, `cosign-key`, `images`, `commit-repo`, `stream`, `tag-pattern`,
`prev_tag`, `curr_tag`, `handwritten`, `output`, `output-env`. Renaming one,
or changing what a default means, breaks every workflow that calls it.

Two facts make that sharper than usual:

- **The default branch is `master`**, with a rename to `main` pending. Both
  workflows trigger on either name so the switch is a settings change; the
  README pin and these lines change with it. GitHub does not keep the old ref
  alive after a rename, so `@master` stops resolving the moment it happens.
- **There are no release tags**, so callers pin a branch (the README says
  which).
  Every consumer therefore tracks this branch's tip: a change is live for them
  the moment it merges, with no version to hold them back and no way to roll
  back except another commit. Cutting a `v1` tag would fix that; until then,
  treat every merge here as an immediate production change.

## Layout and checks

| Path | What |
|---|---|
| `action.yml` | the input contract |
| `changelog.py` | registry/SBOM I/O, diffing, assembly, and CLI |
| `presentation.py` | pure release-data projections and Markdown rendering |
| `test_changelog.py` | the suite — 66 tests |
| `Dockerfile` | the action's runtime |

```bash
python3 -m pytest test_changelog.py -q   # 50 passed
ruff check .                             # config in ruff.toml
```

> **`ruff` is configured but not enforced.** `ruff.toml` sets the rules and
> `test.yml` runs *only* pytest, so nothing checks them and the tree has
> drifted: `ruff check .` reports 7 findings on `master`.
>
> They are style, not defects — 2 `EXE001` shebang bits, 2 `I001` import
> ordering, one unused `warnings` import, one `UP035` typing import, and
> `F841`, an unused `cmd` variable in a test, which changes no behaviour.

## External tools it shells out to

`skopeo` (inspect image manifests and RPM lists), `cosign` (verify the
attestation), plus `oras`, `zstd` and `git`. All are external processes, so
their output format is an implicit dependency: a change in either tool's CLI output can break parsing
without anything in this repo changing. The tests stub these rather than
hitting a registry, which is what makes the suite fast and offline — keep new
tests that way.

## Commit links point at whatever repo `git log` actually ran in

`fetch_commits` runs `git log` in the checkout the action is executing in —
it was never `ublue-os/bluefin`-specific, but `render_changelog` used to
hardcode that URL for every consumer's commit links regardless of whose
commits they were. `resolve_commit_repo_url` fixes the mismatch: explicit
`--commit-repo` wins, then `GITHUB_REPOSITORY` (which Actions sets to the
checked-out repo automatically — normally the right answer with zero
configuration), then the old literal as a last-resort fallback for
non-Actions invocations that set neither. Do not restore the literal as the
first branch; that reintroduces the bug this exists to fix.

## When changing tag discovery

`stream` and `tag-pattern` drive auto-discovery (find the two most recent
`{stream}-YYYYMMDD` tags); `prev_tag`/`curr_tag` are the explicit path and are
**ignored when `stream` is set**. That precedence is easy to invert by
accident, and a workflow that silently compares the wrong pair produces a
changelog that looks plausible and is wrong — which is worse than an error.

## Error classification: `CommandError` and `RECOVERABLE`

`run_cmd` raises `CommandError` (a `RuntimeError`) when a shelled-out tool
exits non-zero. `RECOVERABLE = (CommandError, ValueError, OSError)` is the one
place that defines "this registry, image, tag or payload did not work out":
`retry`, the cosign stable-then-zstd fallback, the ORAS-then-cosign fallback,
the per-payload decode loop, `fetch_commits` and the tag-discovery SBOM probe
all catch that tuple and nothing wider.

Catch `Exception` nowhere in the release path. A `TypeError`, `KeyError` or
`AttributeError` is a defect in this file, and swallowing it as a skipped
payload or retrying it three times produces a changelog that looks plausible
and is missing packages — the same failure mode the tag-precedence note above
warns about. New fetch or parse code either raises one of the `RECOVERABLE`
types or lets the defect out.
