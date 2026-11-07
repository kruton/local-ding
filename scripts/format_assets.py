#!/usr/bin/env python3
"""Format web and POSIX shell assets, or check their formatting in CI."""

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
subprocess.run(
    ["npm", "run", "check:assets" if args.check else "format:assets"],
    cwd=ROOT,
    check=True,
)
shell_files = [
    str(path.relative_to(ROOT))
    for path in sorted((ROOT / "assets").rglob("*"))
    if path.is_file() and path.read_bytes().startswith(b"#!/bin/sh\n")
]
subprocess.run(
    [
        "shfmt",
        "-ln",
        "posix",
        "-i",
        "2",
        "-ci",
        "-sr",
        "-d" if args.check else "-w",
        *shell_files,
    ],
    cwd=ROOT,
    check=True,
)
