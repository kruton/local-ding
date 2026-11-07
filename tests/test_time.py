import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Time(unittest.TestCase):
    def test_dhcp_manual_and_missing_server(self):
        with tempfile.TemporaryDirectory() as temp:
            t = Path(temp)
            ram = t / "ram"
            config = t / "time.json"
            defaults = t / "defaults"
            defaults.write_text(json.dumps({"mode": "auto", "servers": []}))
            bin = t / "bin"
            bin.mkdir()
            for name, body in [
                ("ip", 'echo "default via 192.168.1.1 dev eth0"'),
                ("ntpd", "exec sleep 60"),
            ]:
                p = bin / name
                p.write_text("#!/bin/sh\n" + body + "\n")
                p.chmod(0o755)
            helper = bin / "local-ding-time-config"
            helper.symlink_to(
                Path(__file__).resolve().parents[1] / "payload/local-ding"
            )
            sysroot = os.environ.get("LOCAL_DING_SYSROOT")
            if not sysroot:
                self.skipTest("LOCAL_DING_SYSROOT is required")
            reader = bin / "read-time"
            command = [
                os.environ.get(
                    "LOCAL_DING_QEMU", shutil.which("qemu-arm") or "qemu-arm-static"
                ),
                "-cpu",
                "cortex-a9",
                "-L",
                sysroot,
                str(helper),
            ]
            reader.write_text("#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n')
            reader.chmod(0o755)
            env = {
                **os.environ,
                "LOCAL_DING_TIME_READER": str(reader),
                "PATH": str(bin) + ":" + os.environ["PATH"],
                "LOCAL_DING_TIME_RAM": str(ram),
                "LOCAL_DING_TIME_CONFIG": str(config),
                "LOCAL_DING_TIME_DEFAULTS": str(defaults),
                "LOCAL_DING_NTPD": str(bin / "ntpd"),
                "interface": "eth0",
                "ntpsrv": "192.168.1.1 8.8.8.8",
            }
            script = ROOT / "assets/network/local-ding-time"
            subprocess.run(["sh", str(script), "dhcp", "bound"], env=env, check=True)
            self.assertEqual((ram / "lease-eth0").read_text(), "192.168.1.1")
            p = subprocess.Popen(["sh", str(script), "run"], env=env)

            def wait(test):
                end = time.monotonic() + 8
                while time.monotonic() < end:
                    try:
                        status = json.loads((ram / "status.json").read_text())
                        if test(status):
                            return status
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.05)
                self.fail("time controller status timeout")

            try:
                wait(lambda s: s["running"] and s["servers"] == "192.168.1.1")
                config.write_text(
                    json.dumps({"mode": "manual", "servers": ["10.0.0.1"]})
                )
                wait(lambda s: s["mode"] == "manual" and s["servers"] == "10.0.0.1")
                subprocess.run(
                    ["sh", str(script), "dhcp", "deconfig"], env=env, check=True
                )
                config.write_text(json.dumps({"mode": "auto", "servers": []}))
                wait(lambda s: s["mode"] == "auto" and not s["running"])
            finally:
                p.terminate()
                p.wait(timeout=5)
