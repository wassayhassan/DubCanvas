"""App-owned, repeatable provisioning. Requires only bundled uv and managed Python."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import urllib.request
import zipfile

MSST_REVISION = '84b1eac0887756b4f1a9d7a1ff49105939749ed2'
CORE_EXTRA = 'kokoro==0.9.4'
ROOT = Path(__file__).resolve().parents[1]
_child = None

def progress(message):
    print(message, flush=True)

def run(command, *, env=None, timeout=7200):
    global _child
    progress('Preparing ' + Path(str(command[0])).name)
    _child = subprocess.Popen(list(map(str, command)), env=env, start_new_session=True)
    try:
        status = _child.wait(timeout=timeout)
        if status: raise RuntimeError(f'Setup step failed (exit {status}); see setup log')
    finally:
        if _child.poll() is None:
            os.killpg(_child.pid, signal.SIGTERM)
            try: _child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(_child.pid, signal.SIGKILL); _child.wait()
        _child = None

def interrupted(signum, frame):
    if _child is not None and _child.poll() is None:
        os.killpg(_child.pid, signal.SIGTERM)
    raise KeyboardInterrupt

def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

def core_fingerprint():
    return hashlib.sha256((ROOT / 'requirements.txt').read_bytes() + (ROOT / 'constraints.txt').read_bytes() + CORE_EXTRA.encode()).hexdigest()

def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2)); temp.replace(path)

def download(url, target, *, md5=None):
    target = Path(target)
    if target.exists() and (not md5 or hashlib.md5(target.read_bytes()).hexdigest() == md5): return
    partial = target.with_suffix(target.suffix + '.partial')
    progress('Downloading ' + target.name)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, partial.open('wb') as output:
            count = 0
            while block := response.read(1024 * 1024):
                output.write(block); count += len(block)
                if count % (16 * 1024 * 1024) < len(block): progress(f'{target.name}: {count // (1024 * 1024)} MB downloaded')
        if md5 and hashlib.md5(partial.read_bytes()).hexdigest() != md5:
            raise RuntimeError('Official checkpoint checksum mismatch')
        partial.replace(target)
    finally: partial.unlink(missing_ok=True)

def install_env(uv, directory, version, requirements, env, extra=()):
    python = directory / 'bin/python'
    if not python.exists(): run([uv, 'venv', '--managed-python', '--python', version, '--seed', directory], env=env)
    run([uv, 'pip', 'install', '--python', python, '-r', requirements, *extra], env=env)
    run([python, '-m', 'pip', 'check'], env=env)
    return python

def provision(root, uv, quality=False):
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(root / 'python'), UV_CACHE_DIR=str(root / 'cache'), PYTHONUNBUFFERED='1', PYTHONPATH=str(ROOT))
    progress('Installing the app runtime')
    core = root / 'core/bin/python'
    marker = root / 'core-ready.json'
    try:
        ready = core.exists() and json.loads(marker.read_text()).get('fingerprint') == core_fingerprint()
    except (OSError, ValueError):
        ready = False
    if not ready:
        core = install_env(uv, root / 'core', '3.11', ROOT / 'requirements.txt', env, (CORE_EXTRA,))
    run([core, '-m', 'dubcanvas', '--help'], env=env)
    atomic_json(root / 'core-ready.json', {'fingerprint': core_fingerprint()})
    if not quality: return
    from anime_dubber.quality_models import MODELS, HY_ADAPTER_SHA256, verify_translation_adapter
    import platform
    if platform.machine() != 'arm64': raise RuntimeError('Quality setup requires Apple Silicon')
    # Hardware checked in Swift before expensive downloads and again in runtime preflight.
    candidates = [root / 'models'] + [Path.home() / '.cache/huggingface/hub' / ('models--' + model['repo'].replace('/', '--')) for model in MODELS.values()]
    existing = sum(p.stat().st_size for directory in candidates if directory.exists() for p in directory.rglob('*') if p.is_file() and not p.is_symlink())
    remaining = max(5 * 10**9, 85 * 10**9 - existing)
    if shutil.disk_usage(root).free < remaining: raise RuntimeError(f'Quality setup needs about {remaining / 10**9:.0f} GB more free storage; free space and retry')
    paths = {}
    roles = {'audio': 'mlx_python', 'translation': 'translation_python', 'review': 'review_python', 'separator': 'separator_python', 'torch': 'torch_python'}
    for role, key in roles.items():
        requirement = ROOT / f'requirements-quality-{role}.txt'
        identity = digest_file(requirement)[:16]
        directory = root / 'workers' / f'{role}-{identity}'
        progress(f'Installing and checking {role} runtime')
        python = install_env(uv, directory, '3.12' if role == 'torch' else '3.13', requirement, env)
        run([python, ROOT / 'tools/smoke_quality_runtime.py', role], env=env)
        paths[key] = str(python)
        atomic_json(root / 'worker-paths.json', paths)
    assets = root / 'models'; assets.mkdir(exist_ok=True)
    msst = assets / ('MSST-' + MSST_REVISION)
    if not (msst / 'inference.py').exists():
        archive = assets / 'msst.zip'
        download(f'https://github.com/ZFTurbo/Music-Source-Separation-Training/archive/{MSST_REVISION}.zip', archive)
        unpack = assets / 'msst-unpack'; unpack.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                destination = (unpack / item.filename).resolve()
                if not destination.is_relative_to(unpack.resolve()): raise RuntimeError('Invalid separation archive path')
            z.extractall(unpack)
        extracted = unpack / ('Music-Source-Separation-Training-' + MSST_REVISION)
        if msst.exists(): shutil.rmtree(msst)
        extracted.replace(msst); shutil.rmtree(unpack); archive.unlink()
    checkpoint = assets / 'checkpoint-multi.ckpt'
    download('https://zenodo.org/records/12701995/files/checkpoint-multi.ckpt?download=1', checkpoint, md5='fea2868787551b0cff36cfcf7c3622a3')
    config = msst / 'configs/config_dnr_bandit_v2_mus64.yaml'
    # Use the isolated model runtime for Hub downloads, keeping provisioning stdlib-only.
    translator = MODELS['translator']
    model_path = assets / ('Hy-MT2-' + translator['revision'])
    progress('Downloading translation model (about 32 GB)')
    code = 'from huggingface_hub import snapshot_download; import sys; snapshot_download(sys.argv[1], revision=sys.argv[2], local_dir=sys.argv[3])'
    run([paths['translation_python'], '-c', code, translator['repo'], translator['revision'], model_path], env=env)
    verify_translation_adapter(model_path, HY_ADAPTER_SHA256)
    for role in ('asr', 'verifier', 'aligner', 'tts', 'reviewer', 'fallback'):
        progress('Downloading ' + role + ' model')
        model = MODELS[role]
        run([paths['review_python'] if role == 'reviewer' else paths['mlx_python'], '-c',
             'from huggingface_hub import snapshot_download; import sys; snapshot_download(sys.argv[1], revision=sys.argv[2] or None, allow_patterns=["*.json","*.safetensors","*.txt","*.model","*.tiktoken","*.npz","*.npy","*.ark","*.yaml"])',
             model['repo'], model.get('revision', '')], env=env)
    progress('Checking speaker model access and separation weights')
    run([paths['torch_python'], '-c', 'from pyannote.audio import Pipeline; Pipeline.from_pretrained("pyannote/speaker-diarization-community-1")'], env=env)
    run([paths['separator_python'], '-c', 'import torch,sys; torch.load(sys.argv[1],map_location="cpu",weights_only=True)', checkpoint], env=env)
    settings = dict(paths, msst_path=str(msst), bandit_config=str(config), bandit_checkpoint=str(checkpoint),
                    bandit_config_sha256=digest_file(config), bandit_sha256=digest_file(checkpoint),
                    translation_model_path=str(model_path), translation_adapter_sha256=HY_ADAPTER_SHA256,
                    voice_engine='qwen', review_provider='local', allow_cloud=False)
    # This script's Python is intentionally stdlib-only; preflight runs in the core environment.
    temp = root / 'quality-pending.json'; atomic_json(temp, settings)
    run([core, '-c', 'import json,sys; from anime_dubber.quality_models import setup_issues; issues=setup_issues(json.load(open(sys.argv[1]))); print("\\n".join(issues)); sys.exit(bool(issues))', temp], env=env)
    atomic_json(root / 'quality-settings.json', settings); temp.unlink()
    progress('Quality setup complete. Model performance still requires an actual video test.')

def main():
    import fcntl
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--uv', type=Path, required=True)
    parser.add_argument('--quality', action='store_true')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    root = args.directory.expanduser().resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root / 'setup.lock').open('w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: parser.error('Setup is already running in another DubCanvas window')
        provision(root, args.uv.resolve(), args.quality)
    progress('Setup complete')

if __name__ == '__main__':
    try: main()
    except (Exception, KeyboardInterrupt) as exc:
        progress('Setup stopped: ' + str(exc)); sys.exit(1)
