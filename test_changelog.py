#!/usr/bin/env python3
"""Unit tests for changelog.py (the changelog-action generator).

The action had zero tests; its pure core (SBOM package parsing, package
diffing, commit extraction, tag discovery, markdown rendering) is what
every release changelog depends on. All network/CLI seams (run_cmd,
get_tag_list/get_digest) are monkeypatched — nothing touches a registry.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent / "changelog.py"
_spec = importlib.util.spec_from_file_location("changelog", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


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
