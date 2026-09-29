"""Publish an immutable update archive after the other main-branch checks pass."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def main():
    sha = os.environ["GITHUB_SHA"]
    repository = os.environ["GITHUB_REPOSITORY"]
    required = {"Backend boundary tests", "SwiftUI shell build"}
    for _ in range(80):
        runs = json.loads(run("gh", "api", f"repos/{repository}/actions/runs?head_sha={sha}&event=push&per_page=100"))["workflow_runs"]
        latest = {}
        for item in sorted(runs, key=lambda item: item["id"], reverse=True):
            if item["name"] in required: latest.setdefault(item["name"], item)
        if any(item["status"] == "completed" and item["conclusion"] != "success" for item in latest.values()):
            raise RuntimeError("A required check failed; no update was published")
        if required == set(latest) and all(item["conclusion"] == "success" for item in latest.values()): break
        time.sleep(15)
    else: raise RuntimeError("Required checks did not finish; no update was published")
    if run("gh", "api", f"repos/{repository}/commits/main", "--jq", ".sha") != sha:
        print("Main advanced; the newer build will publish its update")
        return
    root = Path(__file__).resolve().parent.parent
    archive = root / "dist" / f"DubCanvas-update-{sha}.zip"
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(root / "dist/DubCanvas.app"), str(archive))
    manifest = root / "dist/latest-update.json"
    manifest.write_text(json.dumps({"schema": 1, "revision": sha,
        "sourceTimestamp": int(run("git", "show", "-s", "--format=%ct", sha)),
        "version": run("/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(root / "dist/DubCanvas.app/Contents/Info.plist")),
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}, indent=2) + "\n")
    if subprocess.run(["gh", "release", "view", "app-updates", "--repo", repository], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        run("gh", "release", "create", "app-updates", "--repo", repository, "--target", sha, "--prerelease",
            "--title", "DubCanvas automatic updates", "--notes", "Universal updates published after passing CI. Updates preserve local models and projects.")
    # The archive is uploaded before publishing the manifest referencing it.
    run("gh", "release", "upload", "app-updates", str(archive), "--repo", repository, "--clobber")
    run("gh", "release", "upload", "app-updates", str(manifest), "--repo", repository, "--clobber")
    print(f"Published update {sha}")

if __name__ == "__main__": main()
