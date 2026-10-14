"""Copy native tools and their non-system dylib closure into the signed app."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def command(*args): return subprocess.check_output(args, text=True).strip()

def bundle(destination):
    destination.mkdir(parents=True, exist_ok=True)
    library = destination / 'lib'; library.mkdir(exist_ok=True)
    queue = []
    provenance = []
    for tool in ('uv', 'ffmpeg', 'ffprobe', 'rubberband', 'deno'):
        source = shutil.which(tool)
        if not source: raise RuntimeError(f'Install {tool} on the packaging machine')
        target = destination / tool; shutil.copy2(source, target); queue.append(target)
        provenance.append({'tool': tool, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    espeak = Path(command('brew', '--prefix', 'espeak-ng'))
    shutil.copytree(espeak / 'share/espeak-ng-data', destination / 'share/espeak-ng-data', dirs_exist_ok=True)
    source = espeak / 'lib/libespeak-ng.1.dylib'
    target = library / source.name; shutil.copy2(source, target); queue.append(target)
    copied = {target.name: source.resolve()}
    seen = set()
    while queue:
        binary = queue.pop()
        if binary in seen: continue
        seen.add(binary)
        for line in command('otool', '-L', str(binary)).splitlines()[1:]:
            dependency = line.strip().split(' (')[0]
            if dependency.startswith(('/usr/lib/', '/System/Library/', '@')): continue
            source = Path(dependency)
            if not source.exists(): raise RuntimeError(f'Missing dependency {dependency}')
            target = library / source.name
            if source.name in copied and copied[source.name] != source.resolve():
                raise RuntimeError(f'Duplicate library name {source.name}')
            if source.name not in copied:
                copied[source.name] = source.resolve(); shutil.copy2(source, target); queue.append(target)
            replacement = '@loader_path/' + os.path.relpath(target, binary.parent)
            subprocess.run(['install_name_tool', '-change', dependency, replacement, str(binary)], check=True)
        if binary.suffix == '.dylib':
            subprocess.run(['install_name_tool', '-id', '@loader_path/' + binary.name, str(binary)], check=True)
    # Keep formula metadata and available notices with the distributed tools.
    notices = destination / 'licenses'; notices.mkdir(exist_ok=True)
    formulae = {'uv', 'ffmpeg', 'rubberband', 'espeak-ng', 'deno'}
    for source in copied.values():
        parts = source.parts
        if 'Cellar' in parts:
            formulae.add(parts[parts.index('Cellar') + 1])
    for formula in sorted(formulae):
        metadata = json.loads(command('brew', 'info', '--json=v2', formula))
        (notices / (formula + '.json')).write_text(json.dumps(metadata, indent=2))
        prefix = Path(command('brew', '--prefix', formula))
        for candidate in prefix.rglob('*'):
            if candidate.is_file() and candidate.name.lower().startswith(('license', 'copying', 'notice')):
                name = formula + '-' + str(candidate.relative_to(prefix)).replace('/', '-')
                shutil.copy2(candidate, notices / name)
    for binary in seen:
        for line in command('otool', '-L', str(binary)).splitlines()[1:]:
            dependency = line.strip().split(' (')[0]
            if dependency.startswith(('/usr/lib/', '/System/Library/')): continue
            if not dependency.startswith('@loader_path/') or not (binary.parent / dependency.removeprefix('@loader_path/')).exists():
                raise RuntimeError(f'Unresolved portable dependency {dependency} in {binary.name}')
    (destination / 'provenance.json').write_text(json.dumps(provenance, indent=2))
    for tool in ('uv', 'ffmpeg', 'ffprobe', 'rubberband', 'deno'):
        subprocess.run([str(destination / tool), '--version' if tool in ('uv', 'rubberband', 'deno') else '-version'], check=True, stdout=subprocess.DEVNULL)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('destination', type=Path)
    bundle(parser.parse_args().destination)
