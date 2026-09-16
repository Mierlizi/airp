"""Portable Codex, Claude Code, and DeepSeek Harness hook entry point."""
import argparse
from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[1] / 'runtime'
if RUNTIME.is_dir():
    sys.path.insert(0, str(RUNTIME))

from airp.hook import main
from airp.protocol import SUPPORTED_HOSTS


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--host', choices=SUPPORTED_HOSTS, default='codex')
    args = parser.parse_args()
    main(host=args.host)
