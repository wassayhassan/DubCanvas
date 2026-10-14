"""Create isolated quality environments and export their exact resolved locks.

Does not download model weights, execute a remote Hy adapter, or modify legacy environments.
"""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import venv
import shutil

def checked_step(command, timeout, log):
    """Bound setup steps and retain readable diagnostics even on failure."""
    with log.open("a", encoding="utf-8") as handle:
        handle.write("\n$ " + " ".join(map(str, command)) + "\n")
        handle.flush()
        try:
            subprocess.run(command, check=True, timeout=timeout, stdout=handle, stderr=subprocess.STDOUT)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Setup step failed: {command[2:]}; details saved in {log}") from exc

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--role", choices=["audio", "translation", "review", "separator", "torch", "all"], default="all")
    parser.add_argument("--torch-python", default="python3.12", help="Native ARM64 Python 3.12 for Kokoro/diarization")
    parser.add_argument("--step-timeout", type=int, default=1800, help="Seconds allowed per install/check step")
    args = parser.parse_args()
    if args.step_timeout < 1:
        parser.error("--step-timeout must be positive")
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        parser.error("Quality MLX installation requires native ARM64 Python on Apple Silicon")
    if sys.version_info < (3, 13):
        parser.error("Run using ARM64 Python 3.13 or newer")
    root = Path(__file__).resolve().parents[1]
    directory = args.directory.expanduser().resolve()
    roles = ["audio", "translation", "review", "separator", "torch"] if args.role == "all" else [args.role]
    torch_python = shutil.which(args.torch_python)
    if "torch" in roles:
        if not torch_python:
            parser.error("Install native ARM64 Python 3.12 or pass --torch-python; Kokoro 0.9.4 excludes Python 3.13")
        checked = subprocess.run([torch_python, "-c", "import platform,sys; assert platform.machine()=='arm64'; assert sys.version_info[:2]==(3,12)"], check=False)
        if checked.returncode:
            parser.error("--torch-python must be native ARM64 Python 3.12")
    settings = {}
    directory.mkdir(parents=True, exist_ok=True)
    for role in roles:
        destination = directory / role
        if destination.exists():
            parser.error(f"Refusing to overwrite existing environment {destination}; use a new directory")
        if role == "torch":
            subprocess.run([torch_python, "-m", "venv", str(destination)], check=True)
        else:
            venv.EnvBuilder(with_pip=True).create(destination)
        python = destination / "bin/python"
        log = directory / f"{role}.install.log"
        checked_step([str(python), "-m", "pip", "install", "-r", str(root / f"requirements-quality-{role}.txt")], args.step_timeout, log)
        checked_step([str(python), "-m", "pip", "check"], args.step_timeout, log)
        checked_step([str(python), str(root / "tools/smoke_quality_runtime.py"), role], args.step_timeout, log)
        result = subprocess.run([str(python), "-m", "pip", "freeze"], check=True, capture_output=True, text=True)
        (directory / f"{role}.resolved.lock.txt").write_text(result.stdout, encoding="utf-8")
        settings[{"audio": "mlx_python", "translation": "translation_python", "review": "review_python", "separator": "separator_python", "torch": "torch_python"}[role]] = str(python)
        paths = directory / "worker-paths.json"
        previous = json.loads(paths.read_text(encoding="utf-8")) if paths.exists() else {}
        previous.update(settings)
        paths.write_text(json.dumps(previous, indent=2), encoding="utf-8")
    print(directory / "worker-paths.json")

if __name__ == "__main__":
    main()
