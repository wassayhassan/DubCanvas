"""Publish authenticated updates only after all workflows passed for this commit."""

import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def successful_runs(repository, sha):
    required = {
        "Backend boundary tests",
        "SwiftUI shell build",
        "Quality runtime dependency resolution",
        "macOS app bundle",
    }
    runs = json.loads(
        run(
            "gh",
            "api",
            f"repos/{repository}/actions/runs?head_sha={sha}&event=push&per_page=100",
        )
    )["workflow_runs"]
    latest = {}
    for item in sorted(runs, key=lambda row: row["id"], reverse=True):
        if item["name"] in required:
            latest.setdefault(item["name"], item)
    if any(
        item["status"] == "completed" and item["conclusion"] != "success"
        for item in latest.values()
    ):
        raise RuntimeError("A required check failed; no update was published")
    if set(latest) != required or any(
        item["status"] != "completed" or item["conclusion"] != "success"
        for item in latest.values()
    ):
        return None
    return latest


def main():
    try:
        from .update_signing import sign
    except ImportError:
        from update_signing import sign
    sha = os.environ["GITHUB_SHA"]
    repository = os.environ["GITHUB_REPOSITORY"]
    if not successful_runs(repository, sha):
        raise RuntimeError("Required checks are not complete; no update was published")
    if run("gh", "api", f"repos/{repository}/commits/main", "--jq", ".sha") != sha:
        print("Main advanced; the newer build will publish its update")
        return
    root = Path(__file__).resolve().parent.parent
    app = root / "dist/DubCanvas.app"
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info["DubCanvasSourceRevision"] != sha:
        raise RuntimeError("Artifact revision does not match checked commit")
    private = os.environ.get("DUBCANVAS_UPDATE_PRIVATE_KEY", "")
    public = info.get("DubCanvasUpdatePublicKey", "")
    if not private or not public:
        raise RuntimeError(
            "Configure the Ed25519 release key and rebuild; unsigned publication is disabled"
        )
    archive = root / "dist" / f"DubCanvas-update-{sha}.zip"
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive))
    manifest = root / "dist/latest-update.json"
    manifest.write_text(
        json.dumps(
            sign(
                {
                    "schema": 2,
                    "revision": sha,
                    "sourceTimestamp": int(info["DubCanvasSourceTimestamp"]),
                    "version": info["CFBundleShortVersionString"],
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                },
                private,
                public,
            ),
            indent=2,
        )
        + "\n"
    )
    if subprocess.run(
        ["gh", "release", "view", "app-updates", "--repo", repository],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode:
        run(
            "gh",
            "release",
            "create",
            "app-updates",
            "--repo",
            repository,
            "--target",
            sha,
            "--prerelease",
            "--title",
            "DubCanvas automatic updates",
            "--notes",
            "Signed updates published after passing CI.",
        )
    # Immutable archive; overwriting the pointer is safe because its contents are signed.
    assets = json.loads(
        run(
            "gh",
            "release",
            "view",
            "app-updates",
            "--repo",
            repository,
            "--json",
            "assets",
        )
    )["assets"]
    if any(asset["name"] == archive.name for asset in assets):
        with tempfile.TemporaryDirectory() as folder:
            run(
                "gh",
                "release",
                "download",
                "app-updates",
                "--repo",
                repository,
                "--pattern",
                archive.name,
                "--dir",
                folder,
            )
            if (
                hashlib.sha256((Path(folder) / archive.name).read_bytes()).digest()
                != hashlib.sha256(archive.read_bytes()).digest()
            ):
                raise RuntimeError(
                    "An immutable archive already exists with different bytes; refusing to sign or replace it"
                )
    else:
        run(
            "gh", "release", "upload", "app-updates", str(archive), "--repo", repository
        )
    run(
        "gh",
        "release",
        "upload",
        "app-updates",
        str(manifest),
        "--repo",
        repository,
        "--clobber",
    )
    print(f"Published signed update {sha}")


if __name__ == "__main__":
    main()
