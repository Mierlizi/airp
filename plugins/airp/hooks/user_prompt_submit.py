"""Portable Codex plugin entry point using the bundled AIRP runtime."""
from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
if RUNTIME.is_dir():
    sys.path.insert(0, str(RUNTIME))

from airp.hook import main


if __name__ == '__main__':
    main()
