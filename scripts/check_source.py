#!/usr/bin/env python3
"""Check tracked source inputs without examining ignored local firmware output."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    if (
        Path(
            subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=ROOT,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
        != ROOT
    ):
        raise FileNotFoundError()
    paths = [
        ROOT / p
        for p in subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
        if p
    ]
except (subprocess.CalledProcessError, FileNotFoundError):
    paths = [
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and not set(p.relative_to(ROOT).parts)
        & {
            "target",
            "payload",
            "test-payload",
            "dist",
            ".cache",
            ".work",
            "__pycache__",
            ".venv",
            ".ruff_cache",
            "node_modules",
            ".git",
        }
    ]
for p in paths:
    if not p.is_relative_to(ROOT):
        raise SystemExit("Path outside project")
    if p.suffix in {".pak", ".ubi", ".ubifs", ".bin", ".core", ".key", ".pem", ".pyc"}:
        raise SystemExit("Forbidden source input: " + str(p))
    data = p.read_bytes()
    if len(data) > 2 * 1024 * 1024 or b"\0" in data:
        raise SystemExit("Non-source payload: " + str(p))
print("Source input check passes")
