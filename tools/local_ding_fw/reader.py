import ctypes
import struct
import zlib

from . import format as builder


def split(data):
    assert struct.unpack_from("<I", data)[0] == 0x32725913
    expected = struct.unpack_from("<I", data, 4)[0]
    actual = (
        zlib.crc32(
            data[0x7B4:] + struct.pack("<I", 2) + data[12 : 12 + 14 * 64], 0xFFFFFFFF
        )
        ^ 0xFFFFFFFF
    )
    assert expected == actual, "package CRC mismatch"
    result = {}
    previous = 0x7B4
    for i in range(14):
        h = data[12 + i * 64 : 76 + i * 64]
        name = h[:32].split(b"\0")[0].decode()
        if not name:
            continue
        offset, size = struct.unpack_from("<II", h, 56)
        assert offset == previous and offset + size <= len(data)
        result[name] = data[offset : offset + size]
        previous = offset + size
    assert previous == len(data)
    return result


def verify_ubi(data):
    logical = set()
    for off in range(0, len(data), 131072):
        block = data[off : off + 131072]
        assert block[:4] == b"UBI#"
        assert (
            zlib.crc32(block[:60]) ^ 0xFFFFFFFF
            == struct.unpack_from(">I", block, 60)[0]
        )
        vid = struct.unpack_from(">I", block, 16)[0]
        assert block[vid : vid + 4] == b"UBI!"
        assert (
            zlib.crc32(block[vid : vid + 60]) ^ 0xFFFFFFFF
            == struct.unpack_from(">I", block, vid + 60)[0]
        )
        volume, number = struct.unpack_from(">II", block, vid + 8)
        if volume == 0:
            assert number not in logical
            logical.add(number)
    assert logical == set(range(len(logical)))


def read_files(data):
    lzo = ctypes.CDLL("liblzo2.so.2")
    blocks = {}
    for seq, kind, n in builder.nodes(data):
        if kind != 1:
            continue
        ino, key = struct.unpack_from("<II", n, 24)
        size, compression = struct.unpack_from("<IH", n, 40)
        payload = n[48:]
        if compression == 1:
            out = ctypes.create_string_buffer(size)
            length = ctypes.c_size_t(size)
            assert (
                lzo.lzo1x_decompress_safe(
                    payload, len(payload), out, ctypes.byref(length), None
                )
                == 0
            )
            assert length.value == size
            payload = out.raw[:size]
        else:
            assert compression == 0, "unexpected non-LZO compression"
        assert len(payload) == size
        k = ino, key & 0x1FFFFFFF
        if seq > blocks.get(k, (-1, None))[0]:
            blocks[k] = (seq, payload)
    result = {}
    for path, n in builder.entries(data).items():
        ino = struct.unpack_from("<I", n, 24)[0]
        size = struct.unpack_from("<Q", n, 48)[0]
        uid, gid, mode = struct.unpack_from("<III", n, 96)
        content = bytearray(size) if mode & 0xF000 == 0x8000 else None
        if content is not None:
            for (number, index), (_, payload) in blocks.items():
                if number == ino:
                    start = index * 4096
                    content[start : min(size, start + len(payload))] = payload[
                        : max(0, size - start)
                    ]
            content = bytes(content)
        elif mode & 0xF000 == 0xA000:
            content = n[160:]
        result[path] = (uid, gid, mode, content)
    return result
