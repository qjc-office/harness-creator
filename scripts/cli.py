#!/usr/bin/env python3
"""harness-creator CLI 진입점. 본체는 hc/cli.py."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from hc.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
