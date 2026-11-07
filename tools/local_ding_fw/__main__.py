"""Local factory firmware patching; no network access."""

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

from . import format as fmt
from . import network, reader

ROOT = Path(__file__).resolve().parents[2]
FACTORY_SHA = "51b2698da3b0e1ceff3573bb565895f862ab1a1f64b12d786c2f372fa7e67e2e"
NAME = "DB_566128M5MP_W_W.{version}.Reolink-Video-Doorbell-WiFi-W.OV05A10.5MP.WIFI8812.REOLINK.pak"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def private(server):
    import ipaddress

    ip = ipaddress.IPv4Address(server)
    if not any(
        ip in ipaddress.ip_network(n)
        for n in ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    ):
        raise ValueError("NTP requires a private IPv4 address")
    return str(ip)


def extract(files, target):
    target.mkdir()
    for name, (uid, gid, mode, data) in sorted(
        files.items(), key=lambda x: (stat.S_ISLNK(x[1][2]), x[0].count("/"))
    ):
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Unsafe image path")
        p = target / rel
        for parent in p.parents:
            if parent == target:
                break
            if parent.is_symlink():
                raise ValueError("Symlink parent in image")
        p.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISDIR(mode):
            p.mkdir(exist_ok=True)
        elif stat.S_ISREG(mode):
            p.write_bytes(data)
        elif stat.S_ISLNK(mode):
            p.symlink_to(os.fsdecode(data))
        else:
            raise ValueError("Unsupported inode")
        if not p.is_symlink():
            p.chmod(stat.S_IMODE(mode))
        os.chown(p, uid, gid, follow_symlinks=False)


def put(root, name, data, mode=0o644):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() or p.is_symlink():
        if p.is_symlink():
            raise ValueError("Overlay collision with symlink")
    p.write_bytes(data)
    p.chmod(mode)
    os.chown(p, 0, 0)


def install_ssh(root, app):
    binary = (ROOT / "payload/dropbearmulti").read_bytes()
    manifest = json.loads((ROOT / "payload/dropbear.json").read_text())
    if sha(binary) != manifest["sha256"]:
        raise ValueError("Dropbear checksum mismatch")
    if (
        binary[:6] != b"\x7fELF\x01\x01"
        or int.from_bytes(binary[18:20], "little") != 40
    ):
        raise ValueError("ARM Dropbear ELF required")
    put(app, "dropbearmulti", binary, 0o755)
    put(
        app,
        "local-ding-licenses/dropbear-LICENSE",
        (
            ROOT / "payload/licenses" / ("dropbear-" + manifest["version"]) / "LICENSE"
        ).read_bytes(),
    )
    for alias in ["dropbear", "dropbearkey"]:
        path = app / alias
        if path.exists() or path.is_symlink():
            raise ValueError("SSH service path exists")
        path.symlink_to("dropbearmulti")
        os.chown(path, 0, 0, follow_symlinks=False)
    # Home-relative lookup stops permission checks at /root. The vendor's
    # /mnt parents are writable; Dropbear still checks the target .ssh directory.
    home = root / "root"
    if home.is_symlink() or not home.is_dir():
        raise ValueError("Unsupported root home")
    accounts = [
        line.split(":")
        for line in (root / "etc/passwd").read_text().splitlines()
        if line.startswith("root:")
    ]
    if (
        len(accounts) != 1
        or len(accounts[0]) != 7
        or accounts[0][2:4] != ["0", "0"]
        or accounts[0][5:] != ["/root", "/bin/sh"]
    ):
        raise ValueError("Unsupported root account")
    home.chmod(0o700)
    os.chown(home, 0, 0)
    keys = home / ".ssh"
    if keys.exists() or keys.is_symlink():
        raise ValueError("SSH key path exists")
    keys.symlink_to("/mnt/para/dropbear")
    os.chown(keys, 0, 0, follow_symlinks=False)


def overlay(root, app, ntp):
    assets = ROOT / "assets"
    binary = (ROOT / "payload/local-ding").read_bytes()
    manifest = json.loads((ROOT / "payload/manifest.json").read_text())
    if sha(binary) != manifest["sha256"]:
        raise ValueError("Executable checksum mismatch")
    if (
        binary[:5] != b"\x7fELF\x01"
        or binary[5] != 1
        or int.from_bytes(binary[18:20], "little") != 40
    ):
        raise ValueError("ARM ELF required")
    put(app, "local-ding", binary, 0o755)
    install_ssh(root, app)
    for alias in [
        "local-ding-mqtt",
        "local-ding-settings",
        "local-ding-router",
        "local-ding-time-config",
    ]:
        p = app / alias
        if p.exists() or p.is_symlink():
            raise ValueError("Service path exists")
        p.symlink_to("local-ding")
        os.chown(p, 0, 0, follow_symlinks=False)
    for p in (assets / "settings").iterdir():
        if p.suffix in [".html", ".js", ".css"]:
            put(app, "local-settings/" + p.name, p.read_bytes())
    for p in (assets / "init").iterdir():
        put(root, "etc/init.d/" + p.name, p.read_bytes(), 0o755)
    for p in (assets / "network").iterdir():
        dest = (
            ("etc/init.d/" + p.name)
            if p.name.startswith("S")
            else (
                "etc/local-only/web/" + p.name
                if p.name == "render-web.awk"
                else "usr/sbin/"
                + (
                    "local-ding-network"
                    if p.name == "local-only-routes"
                    else ("local-ding-web" if p.name == "local-web" else p.name)
                )
            )
        )
        put(root, dest, p.read_bytes(), 0o644 if p.suffix == ".awk" else 0o755)
    from .templates import web_templates

    for name, data in web_templates((app / "device").read_bytes(), assets).items():
        put(root, "etc/local-only/web/" + name, data)
    put(
        root,
        "etc/local-only/web/settings-locations.conf",
        (assets / "settings/nginx-locations.conf").read_bytes(),
    )
    put(
        root,
        "etc/local-only/blocked-v4",
        ("".join(str(n) + "\n" for n in network.blocked_networks())).encode(),
    )
    put(
        root,
        "etc/local-only/time.json",
        (
            json.dumps({"mode": "manual" if ntp else "auto", "servers": ntp}) + "\n"
        ).encode(),
    )
    start = root / "etc/init.d/start_app"
    text = start.read_text()
    for name in network.DISABLED_BINARIES:
        if text.count("./" + name + " &") != 1:
            raise ValueError("Unsupported startup layout")
        text = text.replace("./" + name + " &", "# Service disabled by local policy")
        (app / name).unlink()
    start.write_text(
        text.replace(
            "#!/bin/sh\n", "#!/bin/sh\n[ -f /mnt/tmp/local-only.ready ] || exit 1\n", 1
        )
    )
    p = app / "dvr.xml"
    text = p.read_text()
    for key in network.DISABLED_CAPABILITIES:
        text, count = re.subn(r"(?m)^" + key + r'="[^"]*"', key + '="0"', text)
        if count != 1:
            raise ValueError("Unsupported capabilities")
    p.write_text(text)
    resolver = root / "etc/resolv.conf"
    if resolver.is_symlink():
        resolver.unlink()
    put(
        root, "etc/resolv.conf", b"nameserver 127.0.0.1\noptions timeout:1 attempts:1\n"
    )
    p = root / "usr/share/udhcpc/default.script"
    text = p.read_text()
    text, count = re.subn(
        r'\n\t\techo "Recreating \$RESOLV_CONF".*?\n\t\t;;',
        '\n\t\t/usr/sbin/local-ding-network install\n\t\t/usr/sbin/local-ding-time dhcp "$1"\n\t\t;;',
        text,
        flags=re.S,
    )
    if count != 1:
        raise ValueError("Unsupported DHCP hook")
    p.write_text(
        text.replace(
            "deconfig)", "deconfig)\n        /usr/sbin/local-ding-time dhcp deconfig", 1
        )
    )
    udhcpc = root / "sbin/udhcpc"
    if not udhcpc.is_symlink():
        raise ValueError("Unsupported DHCP client")
    udhcpc.unlink()
    put(
        root,
        "sbin/udhcpc",
        b'#!/bin/sh\nexec /usr/libexec/local-ding/udhcpc -O 42 "$@"\n',
        0o755,
    )
    p = root / "usr/libexec/local-ding/udhcpc"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.symlink_to("/bin/busybox")
    os.chown(p, 0, 0, follow_symlinks=False)


def verify(package, manifest):
    data = package.read_bytes()
    m = json.loads(manifest.read_text())
    if sha(data) != m["sha256"]:
        raise ValueError("Package checksum mismatch")
    parts = reader.split(data)
    for name in ["rootfs", "app"]:
        reader.verify_ubi(parts[name])
    files = reader.read_files(parts["app"])
    if sha(files["local-ding"][3]) != m["executable_sha256"]:
        raise ValueError("Executable differs")
    if sha(files["dropbearmulti"][3]) != m["dropbear_sha256"]:
        raise ValueError("Dropbear differs")
    for alias in ["dropbear", "dropbearkey"]:
        if not stat.S_ISLNK(files[alias][2]) or files[alias][3] != b"dropbearmulti":
            raise ValueError("Invalid SSH alias")
    root = reader.read_files(parts["rootfs"])
    if root["root"][:3] != (0, 0, stat.S_IFDIR | 0o700):
        raise ValueError("Invalid root home permissions")
    if (
        not stat.S_ISLNK(root["root/.ssh"][2])
        or root["root/.ssh"][3] != b"/mnt/para/dropbear"
    ):
        raise ValueError("Invalid SSH key directory")
    return m


def main():
    if not __debug__:
        raise RuntimeError("Python optimization is unsupported")
    p = argparse.ArgumentParser(prog="local-ding-fw")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("patch")
    a.add_argument("--factory", type=Path, required=True)
    a.add_argument("--build-version", required=True)
    a.add_argument("--ntp-server", action="append", default=[])
    a.add_argument("--output-dir", type=Path, required=True)
    a.add_argument("--overwrite", action="store_true")
    a = sub.add_parser("verify")
    a.add_argument("package", type=Path)
    a.add_argument("--manifest", type=Path, required=True)
    args = p.parse_args()
    if args.command == "verify":
        print(json.dumps(verify(args.package, args.manifest), indent=2))
        return
    if not os.environ.get("FAKEROOTKEY"):
        raise ValueError("Run patch with fakeroot")
    data = args.factory.read_bytes()
    if sha(data) != FACTORY_SHA:
        raise ValueError("Unsupported factory firmware checksum")
    if (
        not re.fullmatch(r"7106_\d{10}", args.build_version)
        or args.build_version <= "7106_2609241897"
    ):
        raise ValueError("A newer 7106 build version is required")
    ntp = list(dict.fromkeys(private(s) for s in args.ntp_server))
    if len(ntp) > 16:
        raise ValueError("At most 16 NTP servers are supported")
    parts = reader.split(data)
    original = dict(parts)
    records = []
    for i in range(14):
        h = data[12 + i * 64 : 76 + i * 64]
        name = h[:32].split(b"\0")[0].decode()
        if name:
            records.append((i, name, 0, 0))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / NAME.format(version=args.build_version)
    manifest = output.with_suffix(".json")
    if output.resolve() == args.factory.resolve():
        raise ValueError("Input overwrite is forbidden")
    if not args.overwrite and (output.exists() or manifest.exists()):
        raise ValueError("Output exists")
    with tempfile.TemporaryDirectory(prefix="local-ding-fw-") as tmp:
        tmp = Path(tmp)
        root = tmp / "rootfs"
        app = tmp / "app"
        before = {n: reader.read_files(parts[n]) for n in ["rootfs", "app"]}
        extract(before["rootfs"], root)
        extract(before["app"], app)
        overlay(root, app, ntp)
        (app / "version_file").write_text(args.build_version + "\n")
        fmt.BUILD = tmp
        stats = {}
        for name, tree in [("rootfs", root), ("app", app)]:
            parts[name], stats[name] = fmt.rebuild(name, parts[name], tree)
        result = fmt.package(data[:0x7B4], records, parts)
        for name in parts:
            if name not in ["rootfs", "app"] and parts[name] != original[name]:
                raise ValueError("Unrelated component changed")
        # Independently read back every staged inode and payload.
        for name, tree in [("rootfs", root), ("app", app)]:
            decoded = reader.read_files(parts[name])
            reader.verify_ubi(parts[name])
            for rel, (uid, gid, mode, content) in decoded.items():
                f = tree / rel
                st = f.lstat()
                if (st.st_uid, st.st_gid, st.st_mode) != (uid, gid, mode):
                    raise ValueError("Metadata readback differs: " + rel)
                actual = (
                    os.fsencode(os.readlink(f))
                    if f.is_symlink()
                    else (f.read_bytes() if f.is_file() else None)
                )
                if actual != content:
                    raise ValueError("Content readback differs: " + rel)
        report = {
            "sha256": sha(result),
            "factory_sha256": sha(data),
            "executable_sha256": sha((app / "local-ding").read_bytes()),
            "dropbear_sha256": sha((app / "dropbearmulti").read_bytes()),
            "ssh_port": 2222,
            "ssh_authentication": "public-key-only",
            "hardware": "DB_566128M5MP_W_W",
            "version": args.build_version,
            "filesystems": stats,
            "ntp_mode": "manual" if ntp else "auto",
        }
        staged = None
        mtmp = None
        try:
            fd, name = tempfile.mkstemp(prefix=".local-ding-pak-", dir=args.output_dir)
            staged = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(result)
                stream.flush()
                os.fsync(stream.fileno())
            fd, name = tempfile.mkstemp(
                prefix=".local-ding-manifest-", dir=args.output_dir
            )
            mtmp = Path(name)
            with os.fdopen(fd, "w") as stream:
                stream.write(json.dumps(report, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            verify(staged, mtmp)
            os.replace(mtmp, manifest)
            os.replace(staged, output)
        finally:
            for temporary in [staged, mtmp]:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    print(output)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, AssertionError) as e:
        sys.exit(str(e) or "Firmware validation failed")
