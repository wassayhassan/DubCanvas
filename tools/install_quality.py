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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--role", choices=["audio", "translation", "review", "separator", "torch", "all"], default="all")
    args = parser.parse_args()
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        parser.error("Quality MLX installation requires native ARM64 Python on Apple Silicon")
    if sys.version_info < (3, 13):
        parser.error("Run using ARM64 Python 3.13 or newer")
    root = Path(__file__).resolve().parents[1]
    directory = args.directory.expanduser().resolve()
    roles = ["audio", "translation", "review", "separator", "torch"] if args.role == "all" else [args.role]
    settings = {}
    for role in roles:
        destination = directory / role
        if destination.exists():
            parser.error(f"Refusing to overwrite existing environment {destination}; use a new directory")
        venv.EnvBuilder(with_pip=True).create(destination)
        python = destination / "bin/python"
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / f"requirements-quality-{role}.txt")], check=True)
        subprocess.run([str(python), "-m", "pip", "check"], check=True)
        result = subprocess.run([str(python), "-m", "pip", "freeze"], check=True, capture_output=True, text=True)
        (directory / f"{role}.resolved.lock.txt").write_text(result.stdout)
        settings[{"audio": "mlx_python", "translation": "translation_python", "review": "review_python", "separator": "separator_python", "torch": "torch_python"}[role]] = str(python)
    (directory / "worker-paths.json").write_text(json.dumps(settings, indent=2))
    print(directory / "worker-paths.json")

if __name__ == "__main__":
    main()
