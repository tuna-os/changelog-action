"""Pure release-data projections and changelog rendering."""


# Release variant labels for the changelog heading.
VARIANT_LABELS = {
    "stable": "Stable Release",
    "lts": "LTS Release",
    "gts": "GTS Release",
    "beta": "Beta Release",
}

FEATURED_PACKAGES = {
    "kernel": "kernel",
    "gnome": "gnome-shell",
    "mesa": "mesa",
    "podman": "podman",
    "nvidia": "akmod-nvidia",
    "docker": "docker",
    "systemd": "systemd",
    "bootc": "bootc",
}

# Used only when the standalone CLI has neither --commit-repo nor the Actions
# GITHUB_REPOSITORY environment variable.
DEFAULT_COMMIT_REPO_URL = "https://github.com/ublue-os/bluefin"


def infer_variant_label(tag: str) -> str:
    """Derive a human-readable release label from a release tag."""
    prefix = tag.split("-")[0].lower()
    return VARIANT_LABELS.get(prefix, f"{prefix.upper()} Release")


def render_changelog(data: dict, handwritten: str = "") -> str:
    """Render release data as Markdown without performing external I/O."""
    prev_tag = data["prev-tag"]
    curr_tag = data["curr-tag"]
    variant_label = infer_variant_label(curr_tag)

    lines = [f"# 🦕 {curr_tag}: {variant_label}", ""]

    if handwritten:
        lines.append(handwritten)
        lines.append("")

    lines.extend(
        [
            f"This is an automatically generated changelog for release `{curr_tag}`.",
            "",
            f"From previous version `{prev_tag}` there have been the following changes. **Only packages that actually changed are shown.**",
            "",
        ]
    )

    for img, pkg_diff in data["diff"].items():
        lines.append(f"## 📦 {img} Packages")
        lines.append("")

        if pkg_diff.get("added"):
            lines.append("### ✨ Added")
            lines.append("| Package | Version |")
            lines.append("| --- | --- |")
            for name, version in pkg_diff["added"].items():
                lines.append(f"| {name} | {version} |")
            lines.append("")

        if pkg_diff.get("removed"):
            lines.append("### ❌ Removed")
            lines.append("| Package | Version |")
            lines.append("| --- | --- |")
            for name, version in pkg_diff["removed"].items():
                lines.append(f"| {name} | {version} |")
            lines.append("")

        if pkg_diff.get("changed"):
            lines.append("### 🔄 Changed")
            lines.append("| Package | Version |")
            lines.append("| --- | --- |")
            for name, changes in pkg_diff["changed"].items():
                lines.append(f"| {name} | {changes['from']} ➡️ {changes['to']} |")
            lines.append("")

    commits = data.get("commits", [])
    if commits:
        repo_url = data.get("commit-repo-url") or DEFAULT_COMMIT_REPO_URL
        lines.append("## 📜 Commits")
        lines.append("| Hash | Subject | Author |")
        lines.append("| --- | --- | --- |")
        for commit in commits:
            short_hash = commit["hash"][:7]
            lines.append(
                f"| 🔹 **[{short_hash}]({repo_url}/commit/{commit['hash']})** | {commit['subject']} | {commit['author']} |"
            )
        lines.append("")

    return "\n".join(lines)


def extract_featured(packages: dict) -> dict:
    """Project packages present in the configured featured-package set."""
    featured = {}
    for label, pkg_name in FEATURED_PACKAGES.items():
        if pkg_name in packages:
            featured[label] = packages[pkg_name]
    return featured


def build_website_data(curr_release: dict) -> dict:
    """Build the small website-facing projection of a release."""
    return {
        img: {"featured": extract_featured(data["packages"])}
        for img, data in curr_release.items()
    }
