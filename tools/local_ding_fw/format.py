import struct
import subprocess
import zlib
from pathlib import Path

# The caller supplies its temporary rebuild directory.
BUILD: Path | None = None

PEB = 131072
LEB = 126976


def nodes(data):
    pos = 0
    while True:
        off = data.find(b"\x31\x18\x10\x06", pos)
        if off < 0:
            return
        pos = off + 4
        if off + 24 > len(data):
            continue
        crc, seq, length, kind = struct.unpack_from("<IQIB", data, off + 4)
        if length < 24 or off + length > len(data):
            continue
        node = data[off : off + length]
        if zlib.crc32(node[8:]) ^ 0xFFFFFFFF == crc:
            yield seq, kind, node


def entries(data):
    inodes = {}
    dents = {}
    for seq, kind, n in nodes(data):
        if kind == 0:
            ino = struct.unpack_from("<I", n, 24)[0]
            if seq > inodes.get(ino, (-1, None))[0]:
                inodes[ino] = (seq, n)
        elif kind == 2:
            parent = struct.unpack_from("<I", n, 24)[0]
            target = struct.unpack_from("<Q", n, 40)[0]
            length = struct.unpack_from("<H", n, 50)[0]
            key = parent, n[56 : 56 + length].decode()
            if seq > dents.get(key, (-1, None))[0]:
                dents[key] = (seq, target)
    paths = {1: ""}
    for _ in range(50):
        for (parent, name), (_, ino) in dents.items():
            if parent in paths and ino:
                paths[ino] = "/".join(filter(None, [paths[parent], name]))
    return {name: inodes[ino][1] for ino, name in paths.items() if ino in inodes}


def rebuild(label, source, stage):
    if BUILD is None:
        raise RuntimeError("A temporary rebuild directory is required")
    superblock = next(n for _, kind, n in nodes(source) if kind == 6)
    minio, leb, oldcnt, maxcnt = struct.unpack_from("<IIII", superblock, 32)
    journal = struct.unpack_from("<Q", superblock, 48)[0]
    log, lpt, orph = struct.unpack_from("<III", superblock, 56)
    fanout = struct.unpack_from("<I", superblock, 72)[0]
    assert minio == 2048 and leb == LEB
    image = BUILD / (label + ".ubifs")
    command = [
        "mkfs.ubifs",
        "-r",
        str(stage),
        "-o",
        str(image),
        "-m",
        str(minio),
        "-e",
        str(leb),
        "-c",
        str(maxcnt),
        "-j",
        str(journal),
        "-l",
        str(log),
        "-p",
        str(orph),
        "-f",
        str(fanout),
        "-x",
        "lzo",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    (BUILD / (label + "-mkfs.log")).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(label + ": " + result.stderr)
    rebuilt = image.read_bytes()
    count = len(rebuilt) // LEB
    assert len(rebuilt) % LEB == 0 and count <= maxcnt
    # Preserve EC/VID headers and both stock layout-volume copies. Extend the
    # rootfs update's populated LEBs if necessary, without resizing its volume.
    blocks = []
    template = None
    existing = set()
    for off in range(0, len(source), PEB):
        block = bytearray(source[off : off + PEB])
        assert block[:4] == b"UBI#"
        vid, dataoff = struct.unpack_from(">II", block, 16)
        assert block[vid : vid + 4] == b"UBI!"
        volume, num = struct.unpack_from(">II", block, vid + 8)
        if volume == 0:
            assert block[vid + 5] == 1
            template = bytes(block[:dataoff])
            existing.add(num)
            block[dataoff:] = rebuilt[num * LEB : (num + 1) * LEB].ljust(LEB, b"\xff")
        blocks.append(bytes(block))
    assert existing == set(range(oldcnt)) and template
    for num in range(oldcnt, count):
        header = bytearray(template)
        vid, dataoff = struct.unpack_from(">II", header, 16)
        struct.pack_into(">I", header, vid + 12, num)
        struct.pack_into(
            ">I", header, vid + 60, zlib.crc32(header[vid : vid + 60]) ^ 0xFFFFFFFF
        )
        blocks.append(bytes(header) + rebuilt[num * LEB : (num + 1) * LEB])
    ubi = b"".join(blocks)
    assert len(ubi) <= ({"rootfs": 22, "app": 21}[label] << 20)
    (BUILD / (label + ".ubi")).write_bytes(ubi)
    return ubi, {
        "used_lebs": count,
        "maximum_lebs": maxcnt,
        "unused_lebs": maxcnt - count,
        "component_bytes": len(ubi),
        "original_component_bytes": len(source),
        "compression": "lzo",
    }


def package(header, records, payloads):
    result = bytearray(header)
    for i, name, _, _ in records:
        data = payloads[name]
        struct.pack_into("<II", result, 12 + i * 64 + 56, len(result), len(data))
        result.extend(data)
    crc = (
        zlib.crc32(
            result[0x7B4:] + struct.pack("<I", 2) + result[12 : 12 + 14 * 64],
            0xFFFFFFFF,
        )
        ^ 0xFFFFFFFF
    )
    struct.pack_into("<I", result, 4, crc)
    return bytes(result)
