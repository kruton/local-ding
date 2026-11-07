"""Failure-path tests for occupancy, exclusive ownership and malformed frames."""

#!/usr/bin/env python3
import os
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_router_peer import Router

ROOT = Path(__file__).resolve().parents[1]
BINARY = Path(os.environ.get("RUST_BINARY", ROOT / "payload/local-ding")).resolve()


class Safety(unittest.TestCase):
    def test_occupied_slot_and_duplicate_reader(self):
        with tempfile.TemporaryDirectory(prefix="rpc-safety-") as tmp:
            root = Path(tmp)
            events = root / "events"
            events.mkdir(mode=0o700)
            link = root / "local-ding-router"
            link.symlink_to(BINARY)
            qemu = [
                os.environ.get(
                    "LOCAL_DING_QEMU", shutil.which("qemu-arm") or "qemu-arm-static"
                ),
                "-cpu",
                "cortex-a9",
                "-L",
                os.environ["LOCAL_DING_SYSROOT"],
                str(link),
            ]
            env = {
                **os.environ,
                "LOCAL_DING_ROUTER_SOCKET": str(root / "ROUT"),
                "LOCAL_DING_NATIVE_EVENT_DIR": str(events),
            }
            router = Router(root / "ROUT", occupied=True)
            with (root / "log").open("wb") as log:
                p = subprocess.Popen(qemu, env=env, stdout=log, stderr=log)
                try:
                    time.sleep(0.4)
                    self.assertIsNone(p.poll())
                    self.assertFalse(router.registered.is_set())
                    self.assertFalse((events / "subscriber.ready").exists())
                    duplicate = subprocess.run(
                        qemu, env=env, capture_output=True, timeout=3
                    )
                    self.assertEqual(duplicate.returncode, 1)
                finally:
                    p.terminate()
                    p.wait(5)
                    router.close()

    def test_malformed_stream_clears_readiness(self):
        with tempfile.TemporaryDirectory(prefix="rpc-malformed-") as tmp:
            root = Path(tmp)
            events = root / "events"
            events.mkdir(mode=0o700)
            link = root / "local-ding-router"
            link.symlink_to(BINARY)
            qemu = [
                os.environ.get(
                    "LOCAL_DING_QEMU", shutil.which("qemu-arm") or "qemu-arm-static"
                ),
                "-cpu",
                "cortex-a9",
                "-L",
                os.environ["LOCAL_DING_SYSROOT"],
                str(link),
            ]
            router = Router(root / "ROUT")
            env = {
                **os.environ,
                "LOCAL_DING_ROUTER_SOCKET": str(root / "ROUT"),
                "LOCAL_DING_NATIVE_EVENT_DIR": str(events),
            }
            with (root / "log").open("wb") as log:
                p = subprocess.Popen(qemu, env=env, stdout=log, stderr=log)
                try:
                    self.assertTrue(router.registered.wait(5))
                    end = time.monotonic() + 3
                    while (
                        not (events / "subscriber.ready").exists()
                        and time.monotonic() < end
                    ):
                        time.sleep(0.01)
                    self.assertTrue((events / "subscriber.ready").exists())
                    # Invalid bounded body length: reader must disconnect before allocation/read.
                    router.client.sendall(
                        struct.pack(
                            "<9I", 0xA5F32C6D, 7, 0, 0, 51, 144, 0, 0, 0xFFFFFFFF
                        )
                    )
                    end = time.monotonic() + 0.8
                    while (
                        events / "subscriber.ready"
                    ).exists() and time.monotonic() < end:
                        time.sleep(0.01)
                    self.assertFalse((events / "subscriber.ready").exists())
                    self.assertIsNone(p.poll())
                finally:
                    p.terminate()
                    p.wait(5)
                    router.close()


if __name__ == "__main__":
    unittest.main()
