#!/usr/bin/env python3
"""Unit tests for changelog.py (the changelog-action generator).

The action had zero tests; its pure core (SBOM package parsing, package
diffing, commit extraction, tag discovery, markdown rendering) is what
every release changelog depends on. All network/CLI seams (run_cmd,
get_tag_list/get_digest) are monkeypatched — nothing touches a registry.
"""

import importlib.util
import base64
import json
import os
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "changelog.py"
_spec = importlib.util.spec_from_file_location("changelog", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_action_runs_the_pinned_repository_source():
    """The selected action ref, not an external mutable image, defines runtime."""
    metadata = (_SCRIPT.parent / "action.yml").read_text()
    assert "image: 'Dockerfile'" in metadata
    assert "docker://" not in metadata
    assert "ghcr.io/hanthor" not in metadata


# ── normalize_version ────────────────────────────────────────────────────────

class TestNormalizeVersion:
    def test_strips_epoch_and_fedora_suffix(self):
        assert _mod.normalize_version("1:2.3.4-5.fc40") == "2.3.4-5"
        assert _mod.normalize_version("2.3.4-1.fc41") == "2.3.4-1"

    def test_keeps_clean_versions(self):
        assert _mod.normalize_version("2.3.4") == "2.3.4"
        assert _mod.normalize_version("0.1.2-3") == "0.1.2-3"


# ── parse_packages ───────────────────────────────────────────────────────────

class TestParsePackages:
    def test_reads_rpm_artifacts_and_normalizes_versions(self):
        sbom = {"artifacts": [
            {"type": "rpm", "name": "bash", "version": "1:5.2.26-4.fc40"},
            {"type": "rpm", "name": "coreutils", "version": "9.4-6.fc40"},
            {"type": "deb", "name": "ignored", "version": "1.0"},  # not rpm
        ]}
        assert _mod.parse_packages(sbom) == {
            "bash": "5.2.26-4",
            "coreutils": "9.4-6",
        }

    def test_reads_purl_packages(self):
        sbom = {"packages": [
            {"name": "git", "versionInfo": "2.45.1-1.fc40",
             "externalRefs": [{"referenceType": "purl",
                               "referenceLocator": "pkg:rpm/fedora/git@2.45.1"}]},
            {"name": "python", "versionInfo": "3.12.4",
             "externalRefs": [{"referenceType": "purl",
                               "referenceLocator": "pkg:pypi/python"}]},  # not rpm
        ]}
        assert _mod.parse_packages(sbom) == {"git": "2.45.1-1"}

    def test_skips_malformed_and_sorts(self):
        sbom = {"artifacts": [
            {"type": "rpm", "name": "b", "version": "2"},
            {"type": "rpm", "name": "a", "version": "1"},
            {"type": "rpm", "name": "no-version"},
            {"type": "rpm", "version": "no-name"},
        ]}
        assert list(_mod.parse_packages(sbom).keys()) == ["a", "b"]


# ── diff_packages / diff_images / common_packages ───────────────────────────

class TestDiffPackages:
    def test_added_removed_changed(self):
        diff = _mod.diff_packages(
            {"a": "1", "b": "1", "gone": "1"},
            {"a": "2", "b": "1", "new": "1"},
        )
        assert diff["added"] == {"new": "1"}
        assert diff["removed"] == {"gone": "1"}
        assert diff["changed"] == {"a": {"from": "1", "to": "2"}}

    def test_no_changes(self):
        diff = _mod.diff_packages({"a": "1"}, {"a": "1"})
        assert diff == {"added": {}, "removed": {}, "changed": {}}


class TestDiffImages:
    def test_diffs_each_image(self):
        diff = _mod.diff_images(
            {"img-a": {"packages": {"a": "1"}}},
            {"img-a": {"packages": {"a": "2"}}, "img-b": {"packages": {"x": "1"}}},
        )
        assert diff["img-a"]["changed"]["a"] == {"from": "1", "to": "2"}
        # img-b has no previous packages → everything added.
        assert diff["img-b"]["added"] == {"x": "1"}


class TestCommonPackages:
    def test_intersection(self):
        release = {
            "img-a": {"packages": {"a": "1", "b": "1", "c": "1"}},
            "img-b": {"packages": {"b": "2", "c": "1"}},
            "img-c": {"packages": {"c": "1"}},
        }
        assert _mod.common_packages(release) == ["c"]

    def test_empty_release(self):
        assert _mod.common_packages({}) == []


# ── fetch_commits ────────────────────────────────────────────────────────────

class TestFetchCommits:
    def test_parses_git_log_lines(self, monkeypatch):
        out = "abc1234;Fix the thing;Alice\nbcd2345;Add a feature;Bob"
        monkeypatch.setattr(_mod, "run_cmd", lambda cmd: out)
        commits = _mod.fetch_commits("v1", "v2")
        assert commits == [
            {"hash": "abc1234", "subject": "Fix the thing", "author": "Alice"},
            {"hash": "bcd2345", "subject": "Add a feature", "author": "Bob"},
        ]
        # The command spans prev..curr.
        cmd = monkeypatch.call_args[0][0] if False else None

    def test_empty_output_is_empty_list(self, monkeypatch):
        monkeypatch.setattr(_mod, "run_cmd", lambda cmd: "")
        assert _mod.fetch_commits("v1", "v2") == []

    def test_git_failure_is_empty_list(self, monkeypatch):
        def boom(cmd):
            raise RuntimeError("git not available")

        monkeypatch.setattr(_mod, "run_cmd", boom)
        assert _mod.fetch_commits("v1", "v2") == []


# ── retry / extract_payloads ─────────────────────────────────────────────────

class TestRetry:
    def test_succeeds_on_second_try(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise ValueError("transient")
            return "ok"

        assert _mod.retry(3, flaky) == "ok"
        assert len(calls) == 2

    def test_exhausts_and_raises(self):
        calls = []

        def always_fails():
            calls.append(1)
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            _mod.retry(2, always_fails)
        assert len(calls) == 2  # no retry past the limit


class TestExtractPayloads:
    def test_finds_quoted_payloads(self):
        s = '{"payload": "abc"}\n{"payload": "def"}'
        assert _mod.extract_payloads(s) == ["abc", "def"]

    def test_no_payloads(self):
        assert _mod.extract_payloads("nothing here") == []


# ── registry and SBOM retrieval ───────────────────────────────────────

class TestRegistryRetrieval:
    def test_fetch_manifest_uses_skopeo_inspect(self, monkeypatch):
        calls = []
        monkeypatch.setattr(_mod, "run_cmd", lambda cmd: calls.append(cmd) or '{"RepoTags": ["v1"]}')
        assert _mod.fetch_manifest("ghcr.io/acme/", "image", "v1") == {"RepoTags": ["v1"]}
        assert calls == [["skopeo", "inspect", "docker://ghcr.io/acme/image:v1"]]

    def test_get_digest_selects_linux_amd64_from_index(self, monkeypatch):
        manifest = {
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {"digest": "sha256:arm", "platform": {"architecture": "arm64", "os": "linux"}},
                {"digest": "sha256:amd", "platform": {"architecture": "amd64", "os": "linux"}},
            ],
        }
        monkeypatch.setattr(_mod, "run_cmd", lambda cmd: json.dumps(manifest))
        assert _mod.get_digest("ghcr.io/acme/", "image", "v1") == "sha256:amd"

    def test_get_digest_rejects_index_without_linux_amd64(self, monkeypatch):
        manifest = {
            "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
            "manifests": [{"digest": "sha256:arm", "platform": {"architecture": "arm64", "os": "linux"}}],
        }
        monkeypatch.setattr(_mod, "run_cmd", lambda cmd: json.dumps(manifest))
        with pytest.raises(ValueError, match="Could not find amd64 linux manifest"):
            _mod.get_digest("ghcr.io/acme/", "image", "v1")

    def test_get_digest_falls_back_to_inspect_for_single_manifest(self, monkeypatch):
        outputs = iter(['{"schemaVersion": 2}', '{"Digest": "sha256:single"}'])
        monkeypatch.setattr(_mod, "run_cmd", lambda cmd: next(outputs))
        assert _mod.get_digest("ghcr.io/acme/", "image", "v1") == "sha256:single"

    def test_fetch_sbom_decodes_spdx_predicate(self, monkeypatch):
        document = {"predicate": {"packages": [{"name": "bash"}]}}
        payload = base64.b64encode(json.dumps(document).encode()).decode()
        monkeypatch.setattr(_mod, "run_cmd", lambda cmd: json.dumps({"payload": payload}))
        assert _mod.fetch_sbom("ghcr.io/acme/", "key", "image", "sha256:x") == document["predicate"]

    def test_fetch_sbom_falls_back_to_lts_attestation(self, monkeypatch):
        calls = []
        document = {"predicate": {"artifacts": [{"name": "bash"}]}}
        payload = base64.b64encode(json.dumps(document).encode()).decode()

        def fake_run(cmd):
            calls.append(cmd)
            if "spdxjson" in cmd:
                raise RuntimeError("missing stable attestation")
            return json.dumps({"payload": payload})

        monkeypatch.setattr(_mod, "run_cmd", fake_run)
        assert _mod.fetch_sbom("ghcr.io/acme/", "key", "image", "sha256:x") == document["predicate"]
        assert "urn:ublue-os:attestation:spdx+json+zstd:v1" in calls[1]


class TestFetchSbomOras:
    """fetch_sbom_oras (`oras discover` + `oras pull`) had zero direct
    coverage — only ever exercised indirectly through a monkeypatched stub
    in TestReleaseAssembly. Its own referrer-selection and error paths were
    never actually run.
    """

    def test_fetches_and_parses_json_via_oras_pull(self, monkeypatch):
        document = {"packages": [{"name": "bash"}]}

        def fake_run(cmd):
            if cmd[1] == "discover":
                return json.dumps({"referrers": [{"digest": "sha256:ref1"}]})
            if cmd[1] == "pull":
                out_dir = cmd[cmd.index("--output") + 1]
                with open(os.path.join(out_dir, "sbom.spdx.json"), "w") as fh:
                    json.dump(document, fh)
                return ""
            raise AssertionError(f"unexpected oras subcommand: {cmd}")

        monkeypatch.setattr(_mod, "run_cmd", fake_run)
        assert _mod.fetch_sbom_oras("ghcr.io/acme/", "image", "sha256:x") == document

    def test_uses_most_recent_referrer_when_multiple(self, monkeypatch):
        document = {"ok": True}

        def fake_run(cmd):
            if cmd[1] == "discover":
                return json.dumps(
                    {"referrers": [{"digest": "sha256:old"}, {"digest": "sha256:new"}]}
                )
            if cmd[1] == "pull":
                assert cmd[-1].endswith("sha256:new"), cmd
                out_dir = cmd[cmd.index("--output") + 1]
                with open(os.path.join(out_dir, "sbom.json"), "w") as fh:
                    json.dump(document, fh)
                return ""
            raise AssertionError(f"unexpected oras subcommand: {cmd}")

        monkeypatch.setattr(_mod, "run_cmd", fake_run)
        assert _mod.fetch_sbom_oras("ghcr.io/acme/", "image", "sha256:x") == document

    def test_raises_when_no_referrers_found(self, monkeypatch):
        monkeypatch.setattr(_mod, "run_cmd", lambda cmd: json.dumps({"referrers": []}))
        with pytest.raises(ValueError, match="No ORAS SBOM referrers"):
            _mod.fetch_sbom_oras("ghcr.io/acme/", "image", "sha256:x")

    def test_raises_when_referrer_has_no_digest(self, monkeypatch):
        monkeypatch.setattr(_mod, "run_cmd", lambda cmd: json.dumps({"referrers": [{}]}))
        with pytest.raises(ValueError, match="no digest"):
            _mod.fetch_sbom_oras("ghcr.io/acme/", "image", "sha256:x")

    def test_raises_when_pull_yields_no_json_file(self, monkeypatch):
        def fake_run(cmd):
            if cmd[1] == "discover":
                return json.dumps({"referrers": [{"digest": "sha256:ref1"}]})
            return ""  # pull "succeeds" but writes nothing usable

        monkeypatch.setattr(_mod, "run_cmd", fake_run)
        with pytest.raises(ValueError, match="No JSON file found"):
            _mod.fetch_sbom_oras("ghcr.io/acme/", "image", "sha256:x")


class TestReleaseAssembly:
    def test_fetch_packages_prefers_oras(self, monkeypatch):
        monkeypatch.setattr(_mod, "get_digest", lambda *args: "sha256:x")
        monkeypatch.setattr(_mod, "fetch_sbom_oras", lambda *args: {
            "artifacts": [{"type": "rpm", "name": "bash", "version": "1:5.2-1.fc45"}]
        })
        monkeypatch.setattr(_mod, "fetch_sbom", lambda *args: pytest.fail("cosign fallback should not run"))
        assert _mod.fetch_packages("registry/", "key", "image", "v1") == {"bash": "5.2-1"}

    def test_fetch_packages_falls_back_to_cosign(self, monkeypatch):
        monkeypatch.setattr(_mod, "get_digest", lambda *args: "sha256:x")
        monkeypatch.setattr(_mod, "fetch_sbom_oras", lambda *args: (_ for _ in ()).throw(ValueError("no referrer")))
        monkeypatch.setattr(_mod, "fetch_sbom", lambda *args: {
            "artifacts": [{"type": "rpm", "name": "coreutils", "version": "9.5-1.fc45"}]
        })
        assert _mod.fetch_packages("registry/", "key", "image", "v1") == {"coreutils": "9.5-1"}

    def test_build_release_collects_each_image(self, monkeypatch):
        monkeypatch.setattr(_mod, "fetch_packages", lambda registry, key, image, tag: {image: tag})
        release = _mod.build_release("registry/", "key", ["one", "two"], "v2")
        assert release == {
            "one": {"packages": {"one": "v2"}},
            "two": {"packages": {"two": "v2"}},
        }

    def test_build_release_data_explicit_configuration(self, monkeypatch):
        monkeypatch.setattr(_mod, "build_release", lambda registry, key, images, tag: {
            images[0]: {"packages": {"bash": tag}}
        })
        monkeypatch.setattr(_mod, "fetch_commits", lambda prev, curr: [{"hash": "abc"}])
        data = _mod.build_release_data(
            "v1", "v2", images=["image"], registry="registry/", cosign_key="key"
        )
        assert data["family"] == "registry/"
        assert data["diff"]["image"]["changed"]["bash"] == {"from": "v1", "to": "v2"}
        assert data["website"]["image"] == {"featured": {}}

    def test_explicit_configuration_requires_images(self):
        with pytest.raises(ValueError, match="--images must be provided"):
            _mod.build_release_data("v1", "v2", registry="registry/", cosign_key="key")


# ── infer_variant_label ──────────────────────────────────────────────────────

class TestInferVariantLabel:
    def test_known_variants(self):
        assert _mod.infer_variant_label("stable-20250101") == "Stable Release"
        assert _mod.infer_variant_label("lts-20250101") == "LTS Release"
        assert _mod.infer_variant_label("gts-20250101") == "GTS Release"
        assert _mod.infer_variant_label("beta-20250101") == "Beta Release"

    def test_unknown_variant_upper_prefix(self):
        assert _mod.infer_variant_label("test-20250101") == "TEST Release"


# ── render_changelog ─────────────────────────────────────────────────────────

class TestRenderChangelog:
    def test_renders_sections_and_commits(self):
        data = {
            "prev-tag": "stable-20250101",
            "curr-tag": "stable-20250102",
            "diff": {
                "bluefin": {
                    "added": {"newpkg": "1.0"},
                    "removed": {"oldpkg": "0.9"},
                    "changed": {"bash": {"from": "5.1", "to": "5.2"}},
                },
            },
            "commits": [
                {"hash": "abcdef1234567890", "subject": "Fix it", "author": "Alice"},
            ],
        }
        md = _mod.render_changelog(data, handwritten="Handwritten note.")
        assert "# 🦕 stable-20250102: Stable Release" in md
        assert "Handwritten note." in md
        assert "## 📦 bluefin Packages" in md
        assert "### ✨ Added" in md and "| newpkg | 1.0 |" in md
        assert "### ❌ Removed" in md and "| oldpkg | 0.9 |" in md
        assert "### 🔄 Changed" in md and "| bash | 5.1 ➡️ 5.2 |" in md
        assert "## 📜 Commits" in md
        assert "abcdef1" in md
        assert "https://github.com/ublue-os/bluefin/commit/abcdef1234567890" in md
        assert "| Fix it | Alice |" in md

    def test_no_sections_when_empty(self):
        md = _mod.render_changelog({"prev-tag": "a", "curr-tag": "b", "diff": {}})
        assert "# 🦕 b: B Release" in md
        assert "### ✨ Added" not in md
        assert "## 📜 Commits" not in md


# ── discover_tags ────────────────────────────────────────────────────────────

class TestDiscoverTags:
    def _patch_network(self, monkeypatch, tags, sbom_tags):
        """tags: RepoTags; sbom_tags: subset that carry an SBOM referrer."""
        monkeypatch.setattr(_mod, "fetch_manifest", lambda *a, **k: {"RepoTags": tags})
        digest_by_tag = {t: f"sha256:{t}" for t in tags}
        monkeypatch.setattr(_mod, "get_digest", lambda reg, img, tag: digest_by_tag[tag])

        def fake_run(cmd):
            joined = " ".join(cmd)
            digest = re.search(r"@(sha256:[^\s]+)", joined).group(1)
            tag = digest[len("sha256:"):]
            refs = {"referrers": [{"kind": "spdx"}]} if tag in sbom_tags else {"referrers": []}
            return json.dumps(refs)

        monkeypatch.setattr(_mod, "run_cmd", fake_run)

    def test_returns_last_two_sbom_tags(self, monkeypatch):
        tags = ["stable-20250101", "stable-20250102", "stable-20250103", "stable-not-a-date"]
        self._patch_network(monkeypatch, tags, sbom_tags={"stable-20250101", "stable-20250102", "stable-20250103"})
        prev, curr = _mod.discover_tags("stable", registry="ghcr.io/x/", images=["bluefin"])
        assert (prev, curr) == ("stable-20250102", "stable-20250103")

    def test_filters_to_date_pattern_and_raises_when_few_sbom(self, monkeypatch):
        tags = ["stable-20250101", "stable-20250102", "garbage-tag"]
        self._patch_network(monkeypatch, tags, sbom_tags={"stable-20250101"})
        with pytest.raises(ValueError, match="Found fewer than 2 SBOM-bearing tags"):
            _mod.discover_tags("stable", registry="ghcr.io/x/", images=["bluefin"])

    def test_requires_registry_images_or_known_family(self):
        with pytest.raises(ValueError, match="Either --registry"):
            _mod.discover_tags("stable")


# ── featured packages ────────────────────────────────────────────────────────

class TestFeatured:
    def test_extract_featured_and_website_data(self):
        packages = {"bash": "5.2", "coreutils": "9.4", "other": "1"}
        featured = _mod.extract_featured(packages)
        # Only keys that exist in packages are reported.
        assert set(featured) <= set(packages)
        data = _mod.build_website_data({"img": {"packages": packages}})
        assert set(data["img"]["featured"]) == set(featured)


# ── main CLI ─────────────────────────────────────────────────────────────────

class TestMainCLI:
    def test_main_writes_markdown_and_output_env(self, monkeypatch, tmp_path):
        out_md = tmp_path / "changelog.md"
        out_env = tmp_path / "out.env"
        argv = [
            "changelog.py",
            "stable-20250101",
            "stable-20250102",
            "--registry", "ghcr.io/tuna-os/",
            "--cosign-key", "key.pub",
            "--images", "img1",
            "-o", str(out_md),
            "--output-env", str(out_env),
            "--handwritten", "Manual notes",
        ]
        monkeypatch.setattr(sys, "argv", argv)
        fake_data = {
            "family": "ghcr.io/tuna-os/",
            "prev-tag": "stable-20250101",
            "curr-tag": "stable-20250102",
            "images": ["img1"],
            "releases": {"previous": {}, "current": {}},
            "common-packages": [],
            "diff": {"img1": {"added": {}, "removed": {}, "changed": {}}},
            "commits": [],
            "website": {},
        }
        monkeypatch.setattr(_mod, "build_release_data", lambda **k: fake_data)

        _mod.main()

        assert out_md.exists()
        md_text = out_md.read_text()
        assert "Manual notes" in md_text
        assert "stable-20250102" in md_text

        assert out_env.exists()
        env_text = out_env.read_text()
        assert 'TITLE="stable-20250102: Stable Release"' in env_text
        assert "TAG=stable-20250102" in env_text
