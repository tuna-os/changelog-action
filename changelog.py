#!/usr/bin/env python3

import sys
import json
import os
import subprocess
import time
import re
import base64
import argparse
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Any
from presentation import (
    DEFAULT_COMMIT_REPO_URL,
    build_website_data,
    extract_featured,
    infer_variant_label,
    render_changelog,
)

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

IMAGE_CONFIGS = {
    "bluefin": {
        "registry": "ghcr.io/ublue-os/",
        "cosign_key": "https://raw.githubusercontent.com/ublue-os/bluefin/2d50cf0f45d4/cosign.pub",
        "images": ["bluefin", "bluefin-dx"],
    },
    "aurora": {
        "registry": "ghcr.io/ublue-os/",
        "cosign_key": "https://raw.githubusercontent.com/ublue-os/aurora/2d50cf0f45d4/cosign.pub",
        "images": ["aurora", "aurora-dx"],
    },
    "bluefin-lts": {
        "registry": "ghcr.io/ublue-os/",
        "cosign_key": "https://raw.githubusercontent.com/ublue-os/bluefin-lts/a5b68cebd447/cosign.pub",
        "images": ["bluefin", "bluefin-dx"],
    },
    "aurora-lts": {
        "registry": "ghcr.io/ublue-os/",
        "cosign_key": "https://raw.githubusercontent.com/ublue-os/aurora-lts/955f986183fd/cosign.pub",
        "images": ["aurora", "aurora-dx"],
    },
}


RETRIES = 3
RETRY_WAIT_S = 2.0

def resolve_commit_repo_url(commit_repo: str | None) -> str:
    """Resolve the base URL used for commit links in the rendered changelog.

    Precedence: explicit --commit-repo, then GITHUB_REPOSITORY (set
    automatically by GitHub Actions for the checked-out repo — this is what
    `git log` in fetch_commits() actually read from), then the legacy
    ublue-os/bluefin fallback for non-Actions invocations that pass neither.

    --commit-repo accepts either a full URL or a bare "owner/repo" slug, to
    match how GITHUB_REPOSITORY itself is shaped.
    """
    if commit_repo:
        commit_repo = commit_repo.rstrip("/")
        if commit_repo.startswith(("http://", "https://")):
            return commit_repo
        return f"https://github.com/{commit_repo}"
    gh_repo = os.environ.get("GITHUB_REPOSITORY")
    if gh_repo:
        return f"https://github.com/{gh_repo}"
    return DEFAULT_COMMIT_REPO_URL


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------------


class CommandError(RuntimeError):
    """An external command (skopeo, cosign, oras, zstd, git) exited non-zero."""


# Failures that mean "this registry, image, tag or payload did not work out":
# a command exited non-zero (CommandError), a payload did not parse or did not
# contain what it must (ValueError, which covers json.JSONDecodeError,
# binascii.Error and UnicodeDecodeError), or the filesystem/network layer
# refused (OSError). Everything else — TypeError, KeyError, AttributeError —
# is a defect in this script, and retrying it three times or logging it as a
# skipped payload only hides it behind a plausible-looking changelog.
RECOVERABLE = (CommandError, ValueError, OSError)


def run_cmd(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode == 0:
        return result.stdout
    else:
        raise CommandError(
            f"Command failed\nCmd: {cmd}\nExit: {result.returncode}\nErr: {result.stderr}"
        )


def retry(n: int, f: Callable) -> Any:
    for attempt in range(1, n + 1):
        try:
            return f()
        except RECOVERABLE as e:
            if attempt < n:
                log.warning(
                    f"Attempt {attempt}/{n} failed: {e}. Retrying in {RETRY_WAIT_S}s…"
                )
                time.sleep(RETRY_WAIT_S)
            else:
                raise


# ----------------------------------------------------------------------------
# SBOM Fetching
# ----------------------------------------------------------------------------


class TargetConfigError(ValueError):
    """Raised when the inputs do not describe one usable target configuration."""


def resolve_target(
    family: str | None = None,
    registry: str | None = None,
    cosign_key: str | None = None,
    images: list[str] | None = None,
    require_cosign_key: bool = True,
) -> tuple[str, str | None, list[str]]:
    """Resolve one target configuration from the inputs.

    This is the single source of truth for the rule "what counts as a usable
    target". It used to be written out three times -- in main(), in
    discover_tags() and in build_release_data() -- with three different
    conditions, so the same inputs could be accepted in one place, rejected in
    another, and silently completed from a different source in a third.

    Two modes:

    - A known ``family`` supplies registry, cosign key and image list, and any
      of the three may be overridden explicitly (action.yml documents
      ``images`` as an override). Overriding the registry while leaving the
      family's cosign key in place verifies one project's attestations against
      another project's key, so that combination warns.
    - Without a family, the caller must supply the whole set: registry, images
      and (when the caller will verify attestations) the cosign key. A partial
      set is an error rather than something to complete from IMAGE_CONFIGS.

    ``require_cosign_key`` is False for tag discovery, which only lists tags
    and never verifies an attestation.
    """
    if family:
        config = IMAGE_CONFIGS.get(family)
        if config is None:
            raise TargetConfigError(
                f"Unknown --family '{family}'. "
                f"Known families: {list(IMAGE_CONFIGS.keys())}"
            )
        if registry and registry != config["registry"] and not cosign_key:
            log.warning(
                f"--registry overrides family '{family}' but --cosign-key does not: "
                f"attestations will be verified against {config['cosign_key']}."
            )
        return (
            registry or config["registry"],
            cosign_key or config["cosign_key"],
            images or list(config["images"]),
        )

    missing = [
        name
        for name, value in (
            ("--registry", registry),
            ("--cosign-key", cosign_key if require_cosign_key else "-"),
            ("--images", images),
        )
        if not value
    ]
    if missing:
        detail = " and ".join(f"{name} must be provided" for name in missing)
        raise TargetConfigError(
            f"Either --registry + --cosign-key + --images, or a known --family must be "
            f"provided: {detail} when --family is not used. "
            f"Known families: {list(IMAGE_CONFIGS.keys())}"
        )
    return registry, cosign_key, images


def fetch_manifest(registry: str, image: str, tag: str) -> dict:
    def _fetch():
        out = run_cmd(["skopeo", "inspect", f"docker://{registry}{image}:{tag}"])
        return json.loads(out)

    return retry(RETRIES, _fetch)


def get_digest(registry: str, image: str, tag: str) -> str:
    def _fetch():
        out = run_cmd(
            ["skopeo", "inspect", "--raw", f"docker://{registry}{image}:{tag}"]
        )
        manifest = json.loads(out)

        media_type = manifest.get("mediaType", "")
        # If it's an image index or manifest list, find the amd64 manifest digest
        if media_type in (
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
        ):
            for m in manifest.get("manifests", []):
                platform = m.get("platform", {})
                if (
                    platform.get("architecture") == "amd64"
                    and platform.get("os") == "linux"
                ):
                    return m.get("digest")
            raise ValueError(
                f"Could not find amd64 linux manifest in index for {image}:{tag}"
            )

        # Fallback to standard skopeo inspect for the actual digest
        out2 = run_cmd(["skopeo", "inspect", f"docker://{registry}{image}:{tag}"])
        return json.loads(out2).get("Digest")

    return retry(RETRIES, _fetch)


def extract_payloads(s: str) -> list[str]:
    """
    cosign verify-attestation can emit multiple JSON lines, one per attestation.
    Extract all payload values so we can find the SPDX one.
    """
    return re.findall(r'"payload"\s*:\s*"([^"]+)"', s)


def fetch_sbom(registry: str, cosign_key: str, image: str, digest: str) -> dict:
    def _fetch():
        cmd_stable = [
            "cosign",
            "verify-attestation",
            "--type",
            "spdxjson",
            "--key",
            cosign_key,
            f"{registry}{image}@{digest}",
        ]
        cmd_lts = [
            "cosign",
            "verify-attestation",
            "--type",
            "urn:ublue-os:attestation:spdx+json+zstd:v1",
            "--key",
            cosign_key,
            f"{registry}{image}@{digest}",
        ]

        try:
            raw = run_cmd(cmd_stable)
        except RECOVERABLE as e_stable:
            log.debug(
                f"spdxjson attestation failed: {e_stable}. Trying zstd attestation..."
            )
            try:
                raw = run_cmd(cmd_lts)
            except RECOVERABLE as e_lts:
                raise ValueError(
                    f"Failed to fetch any attestation. Stable error: {e_stable} | LTS error: {e_lts}"
                )

        payloads = extract_payloads(raw)
        if not payloads:
            raise ValueError("No payload found in attestation output.")

        # Iterate all payloads and return the first one that contains SPDX artifacts
        for payload_b64 in payloads:
            try:
                payload_bytes = base64.b64decode(payload_b64)
                payload_json = json.loads(payload_bytes.decode("utf-8"))

                if (
                    payload_json.get("predicateType")
                    == "urn:ublue-os:attestation:spdx+json+zstd:v1"
                ):
                    import tempfile

                    zstd_b64 = payload_json.get("predicate", {}).get("payload")
                    if zstd_b64:
                        zstd_bytes = base64.b64decode(zstd_b64)
                        with tempfile.NamedTemporaryFile() as tmp:
                            tmp.write(zstd_bytes)
                            tmp.flush()
                            spdx_json_str = run_cmd(["zstd", "-d", "-c", tmp.name])
                            predicate = json.loads(spdx_json_str)
                    else:
                        continue
                else:
                    predicate = payload_json.get("predicate", {})

                if predicate.get("artifacts") or predicate.get("packages"):
                    return predicate
            except RECOVERABLE as e:
                log.warning(f"Skipping payload that failed to decode: {e}")
                continue

        raise ValueError("No valid SPDX predicate found among attestation payloads.")

    return retry(RETRIES, _fetch)


def fetch_sbom_oras(registry: str, image: str, digest: str) -> dict:
    """
    Fetch an SBOM stored as an OCI referrer via `oras attach`.

    Uses `oras discover` to find referrers with artifact-type
    application/vnd.spdx+json, then `oras blob fetch` to download
    the content.
    """
    import tempfile

    ref = f"{registry}{image}@{digest}"

    # Discover referrers of the given type
    out = run_cmd(
        [
            "oras",
            "discover",
            "--artifact-type",
            "application/vnd.spdx+json",
            "--format",
            "json",
            ref,
        ]
    )
    data = json.loads(out)

    referrers = data.get("referrers", [])
    if not referrers:
        raise ValueError(f"No ORAS SBOM referrers found for {ref}")

    # Take the most recent referrer
    referrer = referrers[-1]
    ref_digest = referrer.get("digest")
    if not ref_digest:
        raise ValueError(f"Referrer has no digest: {referrer}")

    # Pull the referrer artifact to a temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        run_cmd(
            [
                "oras",
                "pull",
                "--output",
                tmpdir,
                f"{registry}{image}@{ref_digest}",
            ]
        )
        import os

        # Find the first .json file in the tmpdir
        json_files = [f for f in os.listdir(tmpdir) if f.endswith(".json")]
        if not json_files:
            raise ValueError(f"No JSON file found after oras pull to {tmpdir}")
        with open(os.path.join(tmpdir, json_files[0])) as fh:
            return json.load(fh)


# ----------------------------------------------------------------------------
# Package Extraction
# ----------------------------------------------------------------------------

EPOCH_PATTERN = re.compile(r"^\d+:")
FEDORA_PATTERN = re.compile(r"\.fc\d+")


def normalize_version(v: str) -> str:
    v = EPOCH_PATTERN.sub("", v)
    v = FEDORA_PATTERN.sub("", v)
    return v


def parse_packages(sbom: dict) -> dict:
    pkg_map = {}
    for artifact in sbom.get("artifacts", []):
        if artifact.get("type") == "rpm":
            name = artifact.get("name")
            version = artifact.get("version")
            if name and version:
                pkg_map[name] = normalize_version(version)
            else:
                log.debug(
                    f"Skipping malformed artifact (missing name or version): {artifact}"
                )

    for pkg in sbom.get("packages", []):
        name = pkg.get("name")
        version = pkg.get("versionInfo")
        is_rpm = False
        for ext in pkg.get("externalRefs", []):
            if ext.get("referenceType") == "purl" and "pkg:rpm" in ext.get(
                "referenceLocator", ""
            ):
                is_rpm = True
                break
        if name and version and is_rpm:
            pkg_map[name] = normalize_version(version)

    return dict(sorted(pkg_map.items()))


def fetch_packages(registry: str, cosign_key: str, image: str, tag: str) -> dict:
    digest = get_digest(registry, image, tag)
    try:
        sbom = retry(RETRIES, lambda: fetch_sbom_oras(registry, image, digest))
    except RECOVERABLE as oras_err:
        log.debug(
            f"ORAS referrer fetch failed for {image}@{digest}: {oras_err}. "
            f"Falling back to cosign attestation..."
        )
        sbom = fetch_sbom(registry, cosign_key, image, digest)
    return parse_packages(sbom)


def build_release(registry: str, cosign_key: str, images: list[str], tag: str) -> dict:
    """Fetch packages for all images in parallel."""
    results = {}

    def _fetch_one(img):
        log.info(f"Fetching packages for {img}:{tag}…")
        return img, fetch_packages(registry, cosign_key, img, tag)

    with ThreadPoolExecutor(max_workers=len(images)) as executor:
        futures = {executor.submit(_fetch_one, img): img for img in images}
        for future in as_completed(futures):
            img, pkgs = future.result()  # raises on error
            results[img] = {"packages": pkgs}

    return results


# ----------------------------------------------------------------------------
# Diff Logic
# ----------------------------------------------------------------------------


def diff_packages(prev_pkgs: dict, curr_pkgs: dict) -> dict:
    prev_keys = set(prev_pkgs.keys())
    curr_keys = set(curr_pkgs.keys())

    added = {k: curr_pkgs[k] for k in curr_keys - prev_keys}
    removed = {k: prev_pkgs[k] for k in prev_keys - curr_keys}
    changed = {
        k: {"from": prev_pkgs[k], "to": curr_pkgs[k]}
        for k in prev_keys & curr_keys
        if prev_pkgs[k] != curr_pkgs[k]
    }

    return {
        "added": dict(sorted(added.items())),
        "removed": dict(sorted(removed.items())),
        "changed": dict(sorted(changed.items())),
    }


def diff_images(prev_release: dict, curr_release: dict) -> dict:
    result = {}
    for img, curr_data in curr_release.items():
        prev_pkgs = prev_release.get(img, {}).get("packages", {})
        result[img] = diff_packages(prev_pkgs, curr_data["packages"])
    return result


def common_packages(release: dict) -> list:
    if not release:
        return []
    package_sets = [set(data["packages"].keys()) for data in release.values()]
    return sorted(set.intersection(*package_sets))


# ----------------------------------------------------------------------------
# Git Commit Extraction
# ----------------------------------------------------------------------------


def fetch_commits(prev_tag: str, curr_tag: str) -> list[dict]:
    try:
        out = run_cmd(
            ["git", "log", "--pretty=format:%H;%s;%an", f"{prev_tag}..{curr_tag}"]
        )
    except RECOVERABLE as e:
        log.warning(
            f"Could not fetch git commits between {prev_tag} and {curr_tag}: {e}"
        )
        return []

    commits = []
    for line in out.strip().splitlines():
        if line:
            parts = line.split(";", 2)
            if len(parts) == 3:
                commits.append(
                    {"hash": parts[0], "subject": parts[1], "author": parts[2]}
                )
    return commits


# ----------------------------------------------------------------------------
# Tag Discovery
# ----------------------------------------------------------------------------


def get_tag_list(registry: str, image: str, tag: str) -> list[str]:
    """Fetch all tags for the given image from the registry."""
    manifest = fetch_manifest(registry, image, tag)
    return manifest.get("RepoTags", [])


def discover_tags(
    stream: str,
    family: str | None = None,
    registry: str | None = None,
    images: list[str] | None = None,
    tag_pattern: str | None = None,
) -> tuple[str, str]:
    """
    Find the latest two tags for the given stream (e.g. 'stable', 'latest').
    Returns (prev_tag, curr_tag).

    Resolves registry and image list from either:
      - explicit registry + images arguments, or
      - IMAGE_CONFIGS[family] (legacy behaviour, requires a known family).
    """
    registry, _cosign_key, images = resolve_target(
        family=family,
        registry=registry,
        images=images,
        require_cosign_key=False,
    )
    image = images[0]

    log.info(f"Discovering tags for stream '{stream}' on {registry}{image}...")
    tags = get_tag_list(registry, image, stream)

    # Filter tags: looking for {stream}-YYYYMMDD or similar patterns.
    # The old script used regex: f"{target}-\d\d\d+" (where target=stable)
    # Examples: stable-20260217

    # We also need to filter out tags ending in .0 if strictly following old logic,
    # but let's just look for the date pattern.

    # Pattern: ^stream[-.](?:\d+\.)?\d{8}(?:\.\d+)?$ (matches stable-20250101, stable-43.20250101, stable-20250101.1, lts.20250101)

    pattern = re.compile(tag_pattern if tag_pattern else rf"^{stream}[-.](?:\d+\.)?\d{{8}}(?:\.\d+)?$")

    filtered_tags = sorted([t for t in tags if pattern.match(t)])

    # Only consider tags that have an SBOM referrer attached via ORAS.
    # This ensures we never try to diff against a build that pre-dates SBOM enablement.
    sbom_tags = []
    for t in filtered_tags:
        try:
            digest = get_digest(registry, image, t)
            out = run_cmd(
                [
                    "oras",
                    "discover",
                    "--artifact-type",
                    "application/vnd.spdx+json",
                    "--format",
                    "json",
                    f"{registry}{image}@{digest}",
                ]
            )
            referrers = json.loads(out).get("referrers", [])
            if referrers:
                sbom_tags.append(t)
            else:
                log.debug(f"Tag {t} has no SBOM referrer — skipping.")
        except RECOVERABLE as e:
            log.debug(f"Could not check SBOM for tag {t}: {e} — skipping.")

    if len(sbom_tags) < 2:
        raise ValueError(
            f"Found fewer than 2 SBOM-bearing tags matching pattern '{pattern.pattern}' "
            f"for {stream}. SBOM tags found: {sbom_tags}. "
            f"All matching tags: {filtered_tags}. "
            f"Builds may not yet have SBOM attachments — wait for at least two SBOM-enabled builds."
        )

    prev = sbom_tags[-2]
    curr = sbom_tags[-1]

    log.info(f"Discovered tags: prev={prev}, curr={curr}")
    return prev, curr



def build_release_data(
    prev_tag: str,
    curr_tag: str,
    family: str | None = None,
    images: list[str] | None = None,
    registry: str | None = None,
    cosign_key: str | None = None,
    commit_repo: str | None = None,
) -> dict:
    # Resolve registry, cosign_key, and images (single source of truth).
    registry, cosign_key, images = resolve_target(
        family=family,
        registry=registry,
        cosign_key=cosign_key,
        images=images,
    )

    prev_release = build_release(registry, cosign_key, images, prev_tag)
    curr_release = build_release(registry, cosign_key, images, curr_tag)
    diff = diff_images(prev_release, curr_release)
    commits = fetch_commits(prev_tag, curr_tag)
    website = build_website_data(curr_release)

    return {
        "family": family or registry,
        "prev-tag": prev_tag,
        "curr-tag": curr_tag,
        "images": images,
        "releases": {"previous": prev_release, "current": curr_release},
        "common-packages": common_packages(curr_release),
        "diff": diff,
        "commits": commits,
        "commit-repo-url": resolve_commit_repo_url(commit_repo),
        "website": website,
    }


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------


def normalize_argv(argv: list[str]) -> list[str]:
    """Drop the padding action.yml emits for unset inputs.

    action.yml builds a fixed-width `args` list in which every optional input
    contributes a slot whether or not it was set, so a Docker invocation that
    sets only `stream` still hands the entrypoint roughly a dozen empty-string
    arguments. argparse would reject them: only two positionals are declared.

    The filter is content-based rather than position-based, which is why it is
    named and testable here instead of buried in main(): it cannot distinguish
    padding from a flag value that happens to be empty or the literal string
    "false", and any change to action.yml's input list changes what it strips.
    """
    return [arg for arg in argv if arg.strip() and arg.strip() != "false"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a changelog between two container image releases using SBOM attestations.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Make prev_tag/curr_tag optional if stream is provided
    parser.add_argument(
        "prev_tag", nargs="?", help="Previous release tag (e.g. stable-20250101)"
    )
    parser.add_argument(
        "curr_tag", nargs="?", help="Current release tag (e.g. stable-20250201)"
    )

    parser.add_argument(
        "--stream",
        help="Release stream (e.g. stable, latest, gnome) to automatically discover tags",
    )

    parser.add_argument(
        "--tag-pattern",
        metavar="REGEX",
        help=(
            "Override the default stream-derived tag regex (e.g. r'^\\d{8}$' for bare date tags). "
            "Only used when --stream is provided."
        ),
    )

    parser.add_argument(
        "--family",
        default=None,
        choices=list(IMAGE_CONFIGS.keys()),
        help=(
            "Known image family (e.g. bluefin, aurora). "
            "Provides registry, cosign-key, and images automatically. "
            "Not required when --registry, --cosign-key, and --images are all set."
        ),
    )
    parser.add_argument(
        "--registry",
        metavar="URL",
        help="Container registry prefix (e.g. ghcr.io/tuna-os/). Required when --family is not set.",
    )
    parser.add_argument(
        "--cosign-key",
        metavar="KEY",
        help="URL or path to cosign public key. Required when --family is not set.",
    )
    parser.add_argument(
        "--images",
        nargs="+",
        metavar="IMAGE",
        help="Image names to compare (e.g. yellowfin albacore). Overrides the family default.",
    )
    parser.add_argument(
        "--commit-repo",
        metavar="OWNER/REPO_OR_URL",
        help=(
            "Repository the '## Commits' section links to, as 'owner/repo' or a full "
            "https URL. Defaults to the GITHUB_REPOSITORY Actions sets for the checked-out "
            "repo, then to a legacy ublue-os/bluefin fallback if neither is available."
        ),
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="FILE",
        help="Output file path (defaults to changelog.md or changelog.json)",
    )
    parser.add_argument(
        "--output-env",
        metavar="FILE",
        help="Output environment file (TITLE=... TAG=...)",
    )
    parser.add_argument(
        "--handwritten",
        help="Optional handwritten text to include at the top of the changelog",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of Markdown",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args(normalize_argv(sys.argv[1:] if argv is None else argv))


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # When the action passes --images as a single space-separated string
    # (e.g. "yellowfin albacore skipjack"), split it into a proper list.
    if args.images and len(args.images) == 1 and " " in args.images[0]:
        args.images = args.images[0].split()

    # Validate up front, through the same resolver the work later uses, so bad
    # input exits cleanly here instead of raising a traceback mid-run.
    try:
        resolve_target(
            family=args.family,
            registry=args.registry,
            cosign_key=args.cosign_key,
            images=args.images,
        )
    except TargetConfigError as err:
        log.error(str(err))
        sys.exit(1)

    # Discover or validate tags
    if args.stream:
        if args.prev_tag or args.curr_tag:
            log.warning(
                "Arguments 'prev_tag' and 'curr_tag' are ignored when '--stream' is provided."
            )
        prev_tag, curr_tag = discover_tags(
            stream=args.stream,
            family=args.family,
            registry=args.registry,
            images=args.images,
            tag_pattern=args.tag_pattern,
        )
    else:
        if not args.prev_tag or not args.curr_tag:
            log.error(
                "Either '--stream' OR 'prev_tag' and 'curr_tag' must be provided."
            )
            sys.exit(1)
        prev_tag = args.prev_tag
        curr_tag = args.curr_tag

    out_file = args.output or ("changelog.json" if args.json else "changelog.md")

    release_data = build_release_data(
        prev_tag=prev_tag,
        curr_tag=curr_tag,
        family=args.family,
        images=args.images,
        registry=args.registry,
        cosign_key=args.cosign_key,
        commit_repo=args.commit_repo,
    )

    if args.json:
        with open(out_file, "w") as f:
            json.dump(release_data, f, indent=2)
        log.info(f"✅ JSON written to {out_file}")
    else:
        rendered_md = render_changelog(release_data, handwritten=args.handwritten)
        with open(out_file, "w") as f:
            f.write(rendered_md)
        log.info(f"✅ Markdown written to {out_file}")

    if args.output_env:
        variant_label = infer_variant_label(curr_tag)
        title = f"{curr_tag}: {variant_label}"
        with open(args.output_env, "w") as f:
            f.write(f'TITLE="{title}"\nTAG={curr_tag}\n')
        log.info(f"✅ Env file written to {args.output_env}")


if __name__ == "__main__":
    main()
