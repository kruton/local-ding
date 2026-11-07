import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from local_ding_fw import format, reader
from local_ding_fw.__main__ import extract, private


class Format(unittest.TestCase):
    def test_package_roundtrip_and_corruption(self):
        header = bytearray(0x7B4)
        struct.pack_into("<I", header, 0, 0x32725913)
        header[12:15] = b"app"
        data = format.package(header, [(0, "app", 0, 0)], {"app": b"synthetic-payload"})
        self.assertEqual(reader.split(data), {"app": b"synthetic-payload"})
        bad = bytearray(data)
        bad[-1] ^= 1
        with self.assertRaises(AssertionError):
            reader.split(bad)

    def test_private_address(self):
        self.assertEqual(private("10.0.0.1"), "10.0.0.1")
        for value in ["8.8.8.8", "example.com", "10.0.0.1;id"]:
            with self.assertRaises(ValueError):
                private(value)

    def test_unsafe_path(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                extract({"../escape": (0, 0, 0o100644, b"x")}, Path(d) / "stage")

    def test_synthetic_ubi_rebuild(self):
        import shutil
        import subprocess

        if not shutil.which("mkfs.ubifs"):
            self.skipTest("mtd-utils required")
        with tempfile.TemporaryDirectory() as temp:
            t = Path(temp)
            source = t / "source"
            source.mkdir()
            (source / "stock.txt").write_text("stock content")
            (source / "link").symlink_to("stock.txt")
            raw = t / "seed.ubifs"
            subprocess.run(
                [
                    "mkfs.ubifs",
                    "-r",
                    str(source),
                    "-o",
                    str(raw),
                    "-m",
                    "2048",
                    "-e",
                    "126976",
                    "-c",
                    "150",
                    "-x",
                    "lzo",
                ],
                check=True,
                capture_output=True,
            )
            image = raw.read_bytes()
            blocks = []
            for i in range(len(image) // 126976):
                b = bytearray(b"\xff" * 131072)
                b[:64] = bytes(64)
                b[:4] = b"UBI#"
                b[4] = 1
                struct.pack_into(">II", b, 16, 2048, 4096)
                struct.pack_into(">I", b, 60, zlib.crc32(b[:60]) ^ 0xFFFFFFFF)
                b[2048:2112] = bytes(64)
                b[2048:2052] = b"UBI!"
                b[2052] = 1
                b[2053] = 1
                struct.pack_into(">II", b, 2056, 0, i)
                struct.pack_into(">I", b, 2108, zlib.crc32(b[2048:2108]) ^ 0xFFFFFFFF)
                b[4096:] = image[i * 126976 : (i + 1) * 126976]
                blocks.append(b)
            original = b"".join(blocks)
            reader.verify_ubi(original)
            before = reader.read_files(original)
            stage = t / "stage"
            extract(before, stage)
            (stage / "new.txt").write_text("service asset")
            format.BUILD = t
            rebuilt, stats = format.rebuild("app", original, stage)
            reader.verify_ubi(rebuilt)
            after = reader.read_files(rebuilt)
            for name, value in before.items():
                self.assertEqual(after[name], value)
            self.assertEqual(after["new.txt"][3], b"service asset")
            self.assertLessEqual(stats["used_lebs"], stats["maximum_lebs"])
