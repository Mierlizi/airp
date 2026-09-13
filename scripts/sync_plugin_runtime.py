"""Copy the tested AIRP package into the self-contained Codex plugin."""
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'airp'
TARGET = ROOT / 'plugins' / 'airp' / 'runtime' / 'airp'


def main() -> None:
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    print(f'Synced {SOURCE} -> {TARGET}')


if __name__ == '__main__':
    main()
