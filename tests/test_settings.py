"""Authenticate real settings requests against a fake local camera API."""

import base64
import http.server
import json
import os
import shutil
import stat
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path("/tmp/local-ding-settings-test")


class Camera(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers["Content-Length"])))[0]
        cmd = b["cmd"]
        if cmd == "Login":
            u = b["param"]["User"]
            self.server.user = u["userName"]
            ok = u["password"] == 'test"\\secret' and u["userName"] in [
                "admin",
                "viewer",
            ]
            r = (
                {"code": 0, "value": {"Token": {"name": "valid-session"}}}
                if ok
                else {"code": 1}
            )
        elif cmd == "GetUser":
            r = {
                "code": 0,
                "value": {
                    "CurUser": {"userName": self.server.user},
                    "User": [
                        {"userName": "admin", "level": "admin"},
                        {"userName": "viewer", "level": "guest"},
                    ],
                },
            }
        else:
            r = {"code": 0, "value": {}}
        data = json.dumps([r]).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class SettingsTest(unittest.TestCase):
    def test_admin_settings(self):
        if DATA.exists():
            raise RuntimeError("Refusing to touch existing test directory")
        binary = ROOT / ".work/mqtt-settings-test"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary = ROOT / ".work/rust-settings-test/local-ding-settings"
        binary.parent.mkdir(parents=True, exist_ok=True)
        if binary.is_symlink():
            binary.unlink()
        binary.symlink_to(
            Path(
                os.environ.get("RUST_BINARY", ROOT / "test-payload/local-ding")
            ).resolve()
        )
        with http.server.ThreadingHTTPServer(("127.0.0.1", 18080), Camera) as camera:
            command = [str(binary)]
            if os.environ.get("TEST_ARM", "1"):
                command = [
                    os.environ.get(
                        "LOCAL_DING_QEMU", shutil.which("qemu-arm") or "qemu-arm-static"
                    ),
                    "-cpu",
                    "cortex-a9",
                    "-L",
                    os.environ["LOCAL_DING_SYSROOT"],
                    str(binary),
                ]
            threading.Thread(target=camera.serve_forever, daemon=True).start()
            p = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            try:
                time.sleep(0.2)

                def call(action, values=None, user="admin", headers=None):
                    body = {"admin_user": user, "admin_password": 'test"\\secret'}
                    body.update(values or {})
                    h = {
                        "Content-Type": "application/json",
                        "X-Local-Ding-Settings": "1",
                        "Host": "camera.local",
                        "Origin": "https://camera.local",
                    }
                    h.update(headers or {})
                    req = urllib.request.Request(
                        "http://127.0.0.1:19588/" + action,
                        data=json.dumps(body).encode(),
                        headers=h,
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=8) as r:
                            return (r.status, json.load(r))
                    except urllib.error.HTTPError as r:
                        return (r.code, json.load(r))

                self.assertEqual(call("status", {"admin_password": "wrong"})[0], 403)
                self.assertEqual(call("status", user="viewer")[0], 403)
                self.assertEqual(
                    call("status", headers={"Origin": "https://other.example"})[0], 403
                )
                self.assertEqual(
                    call("status", headers={"X-Local-Ding-Settings": "0"})[0], 400
                )
                code, status = call("status")
                self.assertEqual(code, 200)
                self.assertFalse(status["configured"])
                values = {
                    "broker_host": "192.168.1.10",
                    "broker_port": "1883",
                    "broker_user": "publisher",
                    "broker_password": "mqtt-test-secret",
                    "topic_prefix": "local-ding/frontdoor",
                    "client_id": "local-ding-frontdoor",
                    "poll_ms": "500",
                }
                self.assertEqual(
                    call("mqtt", {**values, "broker_host": "8.8.8.8"})[0], 400
                )
                self.assertFalse((DATA / "mqtt/events.json").exists())
                self.assertEqual(
                    call("mqtt", {**values, "client_id": "x" * 119})[0], 400
                )
                self.assertEqual(call("mqtt", values)[0], 200)
                config = json.loads((DATA / "mqtt/events.json").read_text())
                self.assertEqual(config["camera_password"], 'test"\\secret')
                self.assertEqual(config["broker_password"], "mqtt-test-secret")
                self.assertEqual(
                    stat.S_IMODE((DATA / "mqtt/events.json").stat().st_mode), 384
                )
                self.assertEqual(
                    call("mqtt", {**values, "broker_password": ""})[0], 200
                )
                self.assertEqual(
                    json.loads((DATA / "mqtt/events.json").read_text())[
                        "broker_password"
                    ],
                    "mqtt-test-secret",
                )
                code, status = call("status")
                self.assertEqual(code, 200)
                self.assertTrue(status["broker_password_set"])
                self.assertNotIn("mqtt-test-secret", json.dumps(status))
                self.assertNotIn('test"', json.dumps(status))
                key = (
                    "ssh-ed25519 "
                    + base64.b64encode(
                        b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 " + bytes(range(32))
                    ).decode()
                )
                self.assertEqual(
                    call("key", {"public_key": 'command="sh" ' + key})[0], 400
                )
                self.assertEqual(call("key", {"public_key": key + " test"})[0], 200)
                self.assertEqual(call("key", {"public_key": key})[0], 200)
                self.assertEqual(
                    (DATA / "dropbear/authorized_keys").read_text(), key + "\n"
                )
                code, status = call("status")
                self.assertEqual(status["authorized_keys"], [key])
                self.assertEqual(
                    stat.S_IMODE((DATA / "dropbear/authorized_keys").stat().st_mode),
                    384,
                )
                self.assertEqual(
                    call("mqtt", {**values, "broker_user": "evil\nextra=field"})[0], 400
                )
                self.assertEqual(
                    (DATA / "dropbear/authorized_keys").read_text(), key + "\n"
                )
                self.assertEqual(
                    call("time", {"mode": "manual", "servers": "8.8.8.8"})[0], 400
                )
                self.assertEqual(
                    call("time", {"mode": "manual", "servers": "192.168.1.1 10.0.0.1"})[
                        0
                    ],
                    200,
                )
                self.assertEqual(
                    (DATA / "local-ding/time.json").stat().st_mode & 0o777, 0o600
                )
                code, current = call("status")
                self.assertEqual(current["time"]["mode"], "manual")
                self.assertEqual(call("time", {"mode": "auto", "servers": ""})[0], 200)
                extra = [
                    "ssh-ed25519 "
                    + base64.b64encode(
                        b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes([n]) * 32
                    ).decode()
                    for n in [33, 34]
                ]
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(
                        pool.map(
                            lambda value: call("key", {"public_key": value})[0], extra
                        )
                    )
                self.assertEqual(results, [200, 200])
                self.assertEqual(
                    set((DATA / "dropbear/authorized_keys").read_text().splitlines()),
                    set([key] + extra),
                )
            finally:
                p.terminate()
                p.wait(timeout=6)
                camera.shutdown()
                shutil.rmtree(DATA)


if __name__ == "__main__":
    unittest.main()
