"""Package only the reviewed, checksum-pinned release files. Never includes runtime files."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def reviewed_files(root=ROOT):
    manifest = json.loads((root / 'SHA256SUMS.json').read_text(encoding='utf-8'))
    result = []
    for name, expected in manifest.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
            raise ValueError('Invalid release path: ' + name)
        if (set(relative.parts) & {'local', 'logs', 'backups', 'diagnostics', '.venv', '__pycache__'} or
            '.local.' in relative.name or relative.suffix in {'.apk', '.pt', '.pyc'}):
            raise ValueError('Runtime/private file is not distributable: ' + name)
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Release path escapes root: ' + name)
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError('Reviewed source changed: ' + name)
        if path.suffix in {'.py', '.ps1', '.bat', '.json', '.md'}:
            text = path.read_text(encoding='utf-8-sig')
            if re.search(r'[A-Za-z]:[\\/](?:Code|soft|Users)[\\/]', text):
                raise ValueError('Development-machine path found: ' + name)
        result.append((name, path))
    return result


def main():
    files = reviewed_files()
    output = ROOT.parent / (ROOT.name + '.zip')
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files:
            archive.write(path, ROOT.name + '/' + name)
        archive.write(ROOT / 'SHA256SUMS.json', ROOT.name + '/SHA256SUMS.json')
    print(output)
    print(f'{len(files) + 1} reviewed files; runtime files excluded')


if __name__ == '__main__':
    main()
