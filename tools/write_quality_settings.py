import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from anime_dubber.quality_models import DEFAULTS

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker-paths", type=Path)
    args = parser.parse_args()
    settings = dict(DEFAULTS)
    if args.worker_paths:
        settings.update(json.loads(args.worker_paths.read_text(encoding="utf-8")))
    if args.output.exists():
        parser.error("Refusing to overwrite existing settings")
    args.output.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__": main()
