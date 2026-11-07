"""Exercise real native/ARM client sockets against bounded fake camera/broker peers."""

import http.server
import json
import os
import shutil
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Camera(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cmd = body[0]["cmd"]
        s = self.server.state
        if cmd == "GetEvents" and s.get("block"):
            s["blocked"] = True
            time.sleep(2)
        if cmd == "Login":
            user = body[0]["param"]["User"]
            assert user == {"userName": "test", "password": 'quote"\\test'}
            reply = [{"code": 0, "value": {"Token": {"name": "test-session"}}}]
            s["logins"] += 1
        elif s.get("fail"):
            reply = [{"code": 1, "error": {"rspCode": -6}}]
        else:
            reply = [
                {
                    "code": 0,
                    "value": {
                        "md": {"support": 1, "alarm_state": s["motion"]},
                        "visitor": {"support": 1, "alarm_state": s["visitor"]},
                        "ai": {
                            k: {"support": int(k != "face"), "alarm_state": s[k]}
                            for k in ["people", "vehicle", "dog_cat", "package", "face"]
                        },
                    },
                }
            ]
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Broker(socketserver.BaseRequestHandler):
    def handle(self):

        def read(n):
            out = b""
            while len(out) < n:
                b = self.request.recv(n - len(out))
                if not b:
                    raise EOFError
                out += b
            return out

        def packet(t, b):
            self.request.sendall(bytes([t, len(b)]) + b)

        try:
            while True:
                h = read(1)[0]
                n = 0
                m = 1
                while True:
                    b = read(1)[0]
                    n += (b & 127) * m
                    if b < 128:
                        break
                    m *= 128
                data = read(n)
                if h == 16:
                    self.server.connects.append(data)
                    if self.server.refuse:
                        return
                    size = struct.unpack("!H", data[10:12])[0]
                    if data[12 : 12 + size].decode().endswith("-doorbell"):
                        self.server.native_socket = self.request
                    packet(32, b"\x00\x00")
                elif h >> 4 == 3:
                    z = struct.unpack("!H", data[:2])[0]
                    topic = data[2 : 2 + z].decode()
                    pid = data[2 + z : 4 + z]
                    payload = data[4 + z :].decode()
                    self.server.events.append((topic, payload, bool(h & 1)))
                    if self.server.drop_next:
                        self.server.drop_next = False
                        return
                    packet(64, pid)
                elif h == 192:
                    self.server.pings += 1
                    packet(208, b"")
                else:
                    raise AssertionError(h)
        except (EOFError, ConnectionError, OSError):
            pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name)
        cls.binary = cls.path / "bridge"
        cls.binary = Path(
            os.environ.get("RUST_BINARY", ROOT / "payload/local-ding")
        ).resolve()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_events_and_reconnect(self):
        with (
            Server(("127.0.0.1", 0), Broker) as broker,
            http.server.ThreadingHTTPServer(("127.0.0.1", 0), Camera) as camera,
        ):
            broker.events = []
            broker.connects = []
            broker.drop_next = False
            broker.pings = 0
            broker.refuse = False
            camera.state = dict(
                motion=0,
                visitor=1,
                people=0,
                vehicle=0,
                dog_cat=0,
                package=0,
                face=0,
                logins=0,
            )
            for s in [broker, camera]:
                threading.Thread(target=s.serve_forever, daemon=True).start()
            events = self.path / "native"
            events.mkdir(mode=448)
            config = self.path / "config"
            config.write_text(
                json.dumps(
                    {
                        "broker_host": "127.0.0.1",
                        "broker_port": broker.server_address[1],
                        "broker_user": "test",
                        "broker_password": "example-only",
                        "camera_user": "test",
                        "camera_password": 'quote"\\test',
                        "camera_port": camera.server_port,
                        "poll_ms": 250,
                    }
                )
            )
            config.chmod(384)
            router = None
            sys.path.insert(0, str(ROOT / "tests"))
            from test_router_peer import Router

            router = Router(self.path / "ROUT")
            command = [str(self.binary)]
            if os.environ.get("TEST_ARM", "1"):
                command = [
                    os.environ.get(
                        "LOCAL_DING_QEMU", shutil.which("qemu-arm") or "qemu-arm-static"
                    ),
                    "-cpu",
                    "cortex-a9",
                    "-L",
                    os.environ["LOCAL_DING_SYSROOT"],
                    str(self.binary),
                ]
            with (self.path / "log").open("w+") as log:
                p = subprocess.Popen(
                    command + [str(config)],
                    stderr=log,
                    env={
                        **os.environ,
                        "LOCAL_DING_NATIVE_EVENT_DIR": str(events),
                        "LOCAL_DING_ROUTER_SOCKET": str(self.path / "ROUT"),
                    },
                )

                def native(state, pid=None):
                    if router:
                        router.notify(state, channel=0 if pid is None else 1)
                        return

                def wait(predicate):
                    end = time.monotonic() + 12
                    while time.monotonic() < end:
                        if predicate():
                            return
                        if p.poll() is not None:
                            raise AssertionError("bridge exited")
                        time.sleep(0.03)
                    raise AssertionError("timeout waiting for expected events")

                def has(s, v):
                    return any((t.endswith(s) and b == v for t, b, _ in broker.events))

                try:
                    wait(
                        lambda: (
                            has("/availability", "online")
                            and has("/doorbell/availability", "online")
                            and has("/visitor/state", "ON")
                        )
                    )
                    self.assertFalse(
                        any(
                            (t.endswith("/doorbell/ring") for t, _, _ in broker.events)
                        ),
                        "startup must not fabricate a ring",
                    )
                    camera.state["visitor"] = 0
                    native(0)
                    wait(lambda: has("/visitor/state", "OFF"))
                    camera.state.update(visitor=1, people=1, package=1, motion=1)
                    native(1)
                    wait(
                        lambda: any(
                            (t.endswith("/doorbell/ring") for t, _, _ in broker.events)
                        )
                    )
                    ring = [x for x in broker.events if x[0].endswith("/doorbell/ring")]
                    self.assertEqual(len(ring), 1)
                    self.assertFalse(ring[0][2])
                    self.assertEqual(json.loads(ring[0][1])["event"], "doorbell")
                    wait(
                        lambda: (
                            has("/person/state", "ON")
                            and has("/package/state", "ON")
                            and has("/motion/state", "ON")
                        )
                    )
                    self.assertTrue(
                        all(
                            (
                                retain
                                for topic, _, retain in broker.events
                                if topic.endswith("/state")
                            )
                        )
                    )
                    camera.state["fail"] = True
                    before = len(broker.events)
                    wait(
                        lambda: any(
                            (
                                t.endswith("/availability") and b == "offline"
                                for t, b, _ in broker.events[before:]
                            )
                        )
                    )
                    camera.state["fail"] = False
                    before = len(broker.events)
                    wait(
                        lambda: any(
                            (
                                t.endswith("/availability") and b == "online"
                                for t, b, _ in broker.events[before:]
                            )
                        )
                    )
                    self.assertEqual(
                        len(
                            [
                                x
                                for x in broker.events
                                if x[0].endswith("/doorbell/ring")
                            ]
                        ),
                        1,
                        "API recovery must not fabricate a ring",
                    )
                    broker.drop_next = True
                    camera.state["vehicle"] = 1
                    wait(lambda: len(broker.connects) >= 3)
                    self.assertEqual(
                        broker.connects[0][7],
                        238,
                        "CONNECT must include authenticated retained QoS1 offline will",
                    )
                    self.assertEqual(
                        len(
                            [
                                x
                                for x in broker.events
                                if x[0].endswith("/doorbell/ring")
                            ]
                        ),
                        1,
                        "MQTT reconnect must not fabricate a ring",
                    )
                    camera.state["block"] = True
                    wait(lambda: camera.state.get("blocked"))
                    start = time.monotonic()
                    native(0)
                    native(2)
                    native(1)
                    wait(
                        lambda: (
                            len(
                                [
                                    x
                                    for x in broker.events
                                    if x[0].endswith("/doorbell/ring")
                                ]
                            )
                            >= 2
                        )
                    )
                    self.assertLess(
                        time.monotonic() - start,
                        1,
                        "native ring waited on the HTTP API",
                    )
                    camera.state["block"] = False
                    native(0, pid=os.getpid() + 999)
                    native(2)
                    time.sleep(0.2)
                    self.assertEqual(
                        len(
                            [
                                x
                                for x in broker.events
                                if x[0].endswith("/doorbell/ring")
                            ]
                        ),
                        2,
                        "invalid channel or held-state change generated a ring",
                    )
                    broker.refuse = True
                    broker.native_socket.shutdown(socket.SHUT_RDWR)
                    native(0)
                    native(2)
                    time.sleep(0.3)
                    native(0)
                    native(2)
                    time.sleep(1.2)
                    self.assertEqual(
                        len(
                            [
                                x
                                for x in broker.events
                                if x[0].endswith("/doorbell/ring")
                            ]
                        ),
                        2,
                    )
                    broker.refuse = False
                    wait(
                        lambda: (
                            len(
                                [
                                    x
                                    for x in broker.events
                                    if x[0].endswith("/doorbell/ring")
                                ]
                            )
                            >= 4
                        )
                    )
                    rings = [
                        json.loads(b)
                        for t, b, _ in broker.events
                        if t.endswith("/doorbell/ring")
                    ]
                    self.assertEqual(
                        len({(r["source_pid"], r["sequence"]) for r in rings}),
                        4,
                        "pending native rings were lost or fabricated",
                    )
                    broker.refuse = True
                    broker.native_socket.shutdown(socket.SHUT_RDWR)
                    for _ in range(70):
                        native(0)
                        native(2)
                    time.sleep(float(os.environ.get("OUTAGE_SECONDS", "1.2")))
                    self.assertEqual(
                        len(
                            [
                                x
                                for x in broker.events
                                if x[0].endswith("/doorbell/ring")
                            ]
                        ),
                        4,
                    )
                    broker.refuse = False
                    wait(
                        lambda: (
                            len(
                                [
                                    x
                                    for x in broker.events
                                    if x[0].endswith("/doorbell/ring")
                                ]
                            )
                            >= 68
                        )
                    )
                    self.assertEqual(
                        len(
                            [
                                x
                                for x in broker.events
                                if x[0].endswith("/doorbell/ring")
                            ]
                        ),
                        68,
                        "pending ring capacity includes the unacknowledged publish",
                    )
                    time.sleep(10.5)
                    self.assertGreater(
                        broker.pings,
                        0,
                        "MQTT keepalive runs while application tasks are idle",
                    )
                    self.assertIsNone(p.poll())
                    replacement = config.with_suffix(".new")
                    replacement.write_text(config.read_text())
                    replacement.chmod(384)
                    replacement.replace(config)
                    p.wait(timeout=5)
                    self.assertEqual(
                        p.returncode,
                        0,
                        "atomic configuration replacement must stop the client for supervisor reload",
                    )
                    if router:
                        p = subprocess.Popen(
                            command + [str(config)],
                            stderr=log,
                            env={
                                **os.environ,
                                "LOCAL_DING_NATIVE_EVENT_DIR": str(events),
                                "LOCAL_DING_ROUTER_SOCKET": str(self.path / "ROUT"),
                            },
                        )
                        marker = events / "subscriber.ready"
                        wait(lambda: marker.exists())
                        os.kill(int(marker.read_text()), signal.SIGKILL)
                        p.wait(timeout=6)
                        self.assertIsNotNone(
                            p.returncode,
                            "reader death must stop the parent for supervisor recovery",
                        )
                finally:
                    p.terminate()
                    p.wait(timeout=5)
                    if router:
                        router.close()
                    log.seek(0)
                    logs = log.read()
                    self.assertNotIn("example-only", logs)
                    self.assertNotIn('quote"', logs)
            config.chmod(420)
            r = subprocess.run(command + [str(config)], capture_output=True, timeout=5)
            self.assertEqual(r.returncode, 2)
            broker.shutdown()
            camera.shutdown()


if __name__ == "__main__":
    unittest.main()
