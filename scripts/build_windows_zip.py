"""Build a source-only Windows installer; never include local runtimes or media."""
import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ('START-HERE.cmd', 'install-windows.ps1', 'setup.cmd', 'setup.ps1', 'start.cmd',
         'install-chrome.cmd', 'install_chrome.py', 'requirements.txt', 'requirements-gpu.txt',
         'install-guide.html', 'windows-guide.html', 'README.md', 'README-WINDOWS.txt', 'LICENSE')


def build(destination):
    files = [ROOT / name for name in FILES]
    files += sorted((ROOT / 'lecture_script').glob('*.py'))
    files += sorted(path for path in (ROOT / 'extension').iterdir()
                    if path.is_file() and path.suffix in ('.js', '.json', '.css', '.html'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            data = path.read_bytes()
            if path.suffix in ('.cmd', '.ps1', '.txt'):
                data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            archive.writestr('Lecture-Script-Windows/' + path.relative_to(ROOT).as_posix(), data)
    print(f'{destination.resolve()} ({destination.stat().st_size:,} bytes, {len(files)} files)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'Lecture-Script-Windows-0.5.1.zip')
    build(parser.parse_args().output)
