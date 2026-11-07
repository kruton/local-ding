#!/usr/bin/env python3
"""Build ARM services using the public toolchain sysroot."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--toolchain", type=Path, default=os.environ.get("LOCAL_DING_TOOLCHAIN"))
p.add_argument("--settings-test", action="store_true")
a = p.parse_args()
if a.toolchain is None:
    p.error("--toolchain or LOCAL_DING_TOOLCHAIN is required; see scripts/toolchain.sh")
cc = a.toolchain.resolve() / "bin/arm-linux-gcc"
if not cc.is_file():
    p.error("Missing ARM compiler")
target = "thumbv7neon-unknown-linux-gnueabihf"
env = os.environ.copy()
env["CARGO_TARGET_THUMBV7NEON_UNKNOWN_LINUX_GNUEABIHF_LINKER"] = str(cc)
env["CARGO_TARGET_THUMBV7NEON_UNKNOWN_LINUX_GNUEABIHF_RUSTFLAGS"] = (
    "-C target-cpu=cortex-a9 -C force-unwind-tables=no -C link-arg=-Wl,--gc-sections -C link-arg=-Wl,--build-id=none -C link-arg=-lgcc_s"
)
subprocess.run(
    [
        "cargo",
        "build",
        "--locked",
        "--release",
        "--target",
        target,
        "-Zbuild-std=std,panic_abort",
        "-Zbuild-std-features=optimize_for_size",
    ]
    + (["--features", "settings-test"] if a.settings_test else []),
    cwd=ROOT,
    env=env,
    check=True,
)
binary = ROOT / "target" / target / "release/local-ding"
out = ROOT / ("test-payload" if a.settings_test else "payload")
out.mkdir(exist_ok=True)
shutil.copyfile(binary, out / "local-ding")
(out / "local-ding").chmod(0o755)
(out / "manifest.json").write_text(
    json.dumps(
        {
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "bytes": binary.stat().st_size,
            "target": target,
            "runtime": "Tokio current-thread",
            "rustc": subprocess.check_output(
                ["rustc", "--version"], cwd=ROOT, env=env, text=True
            ).strip(),
            "std_features": ["optimize_for_size"],
        },
        indent=2,
    )
    + "\n"
)

# Include upstream license texts for the exact resolved dependency versions.
meta = json.loads(
    subprocess.check_output(
        ["cargo", "metadata", "--locked", "--format-version", "1"],
        cwd=ROOT,
        env=env,
        text=True,
    )
)
for package in meta["packages"]:
    if package["source"] is None:
        continue
    source = Path(package["manifest_path"]).parent
    dest = out / "licenses" / (package["name"] + "-" + package["version"])
    dest.mkdir(parents=True, exist_ok=True)
    for file in source.iterdir():
        if file.is_file() and (
            file.name.upper().startswith("LICENSE")
            or file.name.upper().startswith("COPYING")
        ):
            shutil.copyfile(file, dest / file.name)

sysroot = Path(
    subprocess.check_output(
        ["rustc", "--print", "sysroot"], cwd=ROOT, env=env, text=True
    ).strip()
)
licenses = sysroot / "share/doc/rust/licenses"
if licenses.is_dir():
    shutil.copytree(
        licenses, out / "licenses/rust-standard-library", dirs_exist_ok=True
    )

if not a.settings_test:
    from build_dropbear import build

    build(a.toolchain)
