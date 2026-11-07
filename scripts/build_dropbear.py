#!/usr/bin/env python3
"""Build the pinned ARM Dropbear server and host-key generator."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2026.94"
SOURCE_COMMIT = "28216cd9af822732a1549b78621c2ea9a76a0fe5"
SOURCE_SHA256 = "e3dbb92dfe44a439ff6d371eaf0120410e23e0d0aae0c6c365c3489a54b55165"
SOURCE_URL = f"https://github.com/mkj/dropbear/archive/{SOURCE_COMMIT}.tar.gz"


def build(toolchain, archive=None):
    toolchain = toolchain.resolve()
    prefix = toolchain / "bin/arm-linux-"
    if not Path(str(prefix) + "gcc").is_file():
        raise ValueError("Missing ARM compiler")
    cache = ROOT / ".cache/dropbear"
    cache.mkdir(parents=True, exist_ok=True)
    if archive is None:
        archive = cache / f"dropbear-{VERSION}-{SOURCE_COMMIT[:12]}.tar.gz"
        if not archive.exists():
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
                        shutil.copyfileobj(response, stream)
                    stream.close()
                    if (
                        hashlib.sha256(temporary.read_bytes()).hexdigest()
                        != SOURCE_SHA256
                    ):
                        raise ValueError("Dropbear source checksum mismatch")
                    temporary.replace(archive)
                finally:
                    temporary.unlink(missing_ok=True)
    archive = archive.resolve()
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("Dropbear source checksum mismatch")
    work = ROOT / ".work/dropbear"
    work.mkdir(parents=True, exist_ok=True)
    # Extract only checksum-verified upstream sources into an isolated workspace.
    with tempfile.TemporaryDirectory(dir=work) as directory:
        directory = Path(directory)
        subprocess.run(["tar", "-xzf", str(archive), "-C", str(directory)], check=True)
        source = directory / f"dropbear-{SOURCE_COMMIT}"
        output = directory / "build"
        output.mkdir()
        shutil.copyfile(ROOT / "config/dropbear-options.h", output / "localoptions.h")
        env = dict(
            os.environ,
            CC=str(prefix) + "gcc",
            AR=str(prefix) + "ar",
            RANLIB=str(prefix) + "ranlib",
            CFLAGS=f"-Os -mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard -ffunction-sections -fdata-sections -ffile-prefix-map={directory}=/build/dropbear",
            LDFLAGS="-Wl,--gc-sections -Wl,--build-id=none -Wl,--as-needed",
            # Cross configure cannot inspect target devices. The camera uses
            # Linux devpts and libc PTMX helpers, without a shared libutil.
            CPPFLAGS="-DUSE_DEV_PTMX=1",
        )
        with (work / "build.log").open("w") as log:
            for command in [
                [
                    str(source / "configure"),
                    "--host=arm-linux-gnueabihf",
                    "--disable-zlib",
                    "--disable-openpty",
                    "--disable-syslog",
                    "--disable-lastlog",
                    "--disable-loginfunc",
                    "--disable-utmp",
                    "--disable-utmpx",
                    "--disable-wtmp",
                    "--disable-wtmpx",
                ],
                [
                    "make",
                    f"-j{min(os.cpu_count() or 1, 8)}",
                    "PROGRAMS=dropbear dropbearkey",
                    "MULTI=1",
                ],
            ]:
                try:
                    subprocess.run(
                        command,
                        cwd=output,
                        env=env,
                        check=True,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                except subprocess.CalledProcessError:
                    print(
                        f"Dropbear build failed; see {work / 'build.log'}",
                        file=sys.stderr,
                    )
                    raise
        payload = ROOT / "payload"
        payload.mkdir(exist_ok=True)
        binary = payload / "dropbearmulti"
        subprocess.run(
            [str(prefix) + "strip", "-o", str(binary), str(output / "dropbearmulti")],
            check=True,
        )
        binary.chmod(0o755)
        dynamic = subprocess.check_output(
            [str(prefix) + "readelf", "-d", str(binary)], text=True
        )
        libraries = re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic)
        if set(libraries) != {"libc.so.6", "ld-linux-armhf.so.3"}:
            raise ValueError(f"Unsupported Dropbear shared dependencies: {libraries}")
        licenses = payload / "licenses" / f"dropbear-{VERSION}"
        licenses.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "LICENSE", licenses / "LICENSE")
        (payload / "dropbear.json").write_text(
            json.dumps(
                {
                    "version": VERSION,
                    "source_url": SOURCE_URL,
                    "source_commit": SOURCE_COMMIT,
                    "source_sha256": SOURCE_SHA256,
                    "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                    "bytes": binary.stat().st_size,
                    "target": "arm-linux-gnueabihf",
                    "libc": "glibc 2.30",
                    "programs": ["dropbear", "dropbearkey"],
                    "terminal": "Linux PTMX",
                    "shared_libraries": libraries,
                    "options_sha256": hashlib.sha256(
                        (ROOT / "config/dropbear-options.h").read_bytes()
                    ).hexdigest(),
                },
                indent=2,
            )
            + "\n"
        )
        print(f"Dropbear {VERSION}: {binary.stat().st_size:,} bytes (stripped)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--toolchain", type=Path, default=os.environ.get("LOCAL_DING_TOOLCHAIN")
    )
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()
    if args.toolchain is None:
        parser.error("--toolchain or LOCAL_DING_TOOLCHAIN is required")
    build(args.toolchain, args.source_archive)
