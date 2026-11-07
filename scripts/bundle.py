#!/usr/bin/env python3
import hashlib
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "dist"
out.mkdir(exist_ok=True)
manifest = json.loads((ROOT / "payload/manifest.json").read_text())
assert (
    hashlib.sha256((ROOT / "payload/local-ding").read_bytes()).hexdigest()
    == manifest["sha256"]
)
ssh_manifest = json.loads((ROOT / "payload/dropbear.json").read_text())
assert (
    hashlib.sha256((ROOT / "payload/dropbearmulti").read_bytes()).hexdigest()
    == ssh_manifest["sha256"]
)
archive = out / "local-ding-linux.tar.gz"
with tarfile.open(archive, "w:gz") as tar:
    for name in [
        "payload",
        "assets",
        "tools",
        "local-ding-fw",
        "README.md",
        "LICENSE",
        "THIRD-PARTY.md",
        "Dockerfile",
        ".dockerignore",
    ]:
        tar.add(
            ROOT / name,
            arcname="local-ding/" + name,
            filter=lambda i: (
                None if "__pycache__" in i.name or i.name.endswith(".pyc") else i
            ),
        )
(out / "SHA256SUMS").write_text(
    hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n"
)
print(archive)

source = out / "local-ding-source.tar.gz"
with tarfile.open(source, "w:gz") as tar:
    for name in [
        "package.json",
        "package-lock.json",
        ".prettierrc.json",
        ".editorconfig",
        "pyproject.toml",
        "uv.lock",
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "src",
        "config",
        "assets",
        "tools",
        "scripts",
        "tests",
        ".github",
        ".gitignore",
        ".dockerignore",
        "Dockerfile",
        "local-ding-fw",
        "README.md",
        "LICENSE",
        "THIRD-PARTY.md",
    ]:
        tar.add(
            ROOT / name,
            arcname="local-ding/" + name,
            filter=lambda i: (
                None if "__pycache__" in i.name or i.name.endswith(".pyc") else i
            ),
        )
with (out / "SHA256SUMS").open("a") as checks:
    checks.write(
        hashlib.sha256(source.read_bytes()).hexdigest() + "  " + source.name + "\n"
    )
print(source)
