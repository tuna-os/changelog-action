# changelog-action Roadmap

**Last updated**: 2026-08-24 | **Maintainer**: tuna-os (hanthor)

---

## Mission

Generate trustworthy release changelogs for container images: diff the RPM
package sets between two versions (skopeo), verify the attestation (cosign),
and emit a changelog — so every TunaOS release records what changed, verified,
not guessed.

---

## Current Status

- **Role**: composite GitHub Action consumed org-wide via
  `uses: tuna-os/changelog-action@master`.
- **Distribution**: **unversioned** — no tags, no releases; consumers pin to
  the mutable `master` branch.
- **Trust**: action itself verifies cosign attestations, but its cosign/oras
  install is unpinned from 'latest' with no checksum (sec-check #14,
  supersedes #1992).
- **Health**: 3 open issues — pinned cosign/oras install (#14), tag-pattern
  input docs (#12/#13).

### Priorities

| Priority | Item | Tracking | Status |
|----------|------|----------|--------|
| P0 | First tagged release (v1.x) + consumers pin to tag | (new) | ⬜ Not started |
| P1 | Pin cosign/oras install to checksummed versions | #14 | 🟡 Open |
| P2 | tag-pattern input documented | #12/#13 | 🟡 Open |
| P2 | ROADMAP-coverage entry in org ROADMAP tally | #1295 | ⬜ Not started |

---

## Quarterly Goals

### Current Quarter (2026 Q3)

**Theme**: version the verifier

| Goal | Owner | Tracking | Status |
|------|-------|----------|--------|
| Cut v1.x tag; update consumers from `@master` | hanthor | (new) | ⬜ Not started |
| Pin cosign/oras install | hanthor | #14 | ⬜ Not started |

### Next Quarter (2026 Q4)

**Theme**: trust and cadence

| Goal | Owner | Tracking | Status |
|------|-------|----------|--------|
| Release cadence aligned with org | tuna-os | (new) | ⬜ Not started |

---

*ROADMAP added by strategist agent (ACMM L6 — full mode). Signed-off-by: hanthor-hive-agent[bot] <290068839+hanthor-hive-agent[bot]@users.noreply.github.com>*
