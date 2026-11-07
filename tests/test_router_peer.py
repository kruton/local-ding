"""Synthetic native peer for fault/reconnect tests; never contacts /dev/ROUT."""

import socket
import struct
import threading


class Router:
    def __init__(self, path, occupied=False):
        self.path = str(path)
        self.occupied = occupied
        self.client = None
        self.error = None
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(self.path)
        self.socket.listen(1)
        self.socket.settimeout(0.1)
        self.stop = threading.Event()
        self.registered = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def send(self, seq, msg, data=b"", ack=False, result=0, flags=0, source=51):
        body = struct.pack("<IIQI", msg, 0, 2**64 - 1, len(data)) + data + b"\0" * 4
        packet = (
            struct.pack(
                "<9I",
                0xA5F32C6D,
                seq,
                0,
                flags,
                source,
                144,
                int(ack),
                result & 0xFFFFFFFF,
                len(body),
            )
            + body
        )
        with self.lock:
            self.client.sendall(packet)

    def notify(self, state, channel=0, message=14016):
        assert self.registered.wait(5), "subscriber did not register"
        self.send(0xFFFFFFFF, message, struct.pack("<II", channel, state), source=105)

    def run(self):
        def exact(n):
            data = b""
            while len(data) < n:
                if self.stop.is_set():
                    raise EOFError
                try:
                    block = self.client.recv(n - len(data))
                except socket.timeout:
                    continue
                if not block:
                    raise EOFError
                data += block
            return data

        try:
            while not self.stop.is_set():
                try:
                    self.client, _ = self.socket.accept()
                except socket.timeout:
                    continue
                self.client.settimeout(0.1)
                try:
                    while not self.stop.is_set():
                        h = struct.unpack("<9I", exact(36))
                        assert h[0] == 0xA5F32C6D and 24 <= h[8] <= 32768
                        body = exact(h[8])
                        msg = struct.unpack_from("<I", body)[0]
                        n = struct.unpack_from("<I", body, 16)[0]
                        data = body[20 : 20 + n]
                        assert h[4] == 144 and h[5] == 51 and n + 24 == len(body)
                        if msg == 5:
                            assert data == struct.pack("<I", 144)
                            self.send(
                                h[1],
                                5,
                                ack=True,
                                result=0
                                if self.occupied or self.registered.is_set()
                                else -1,
                            )
                        elif msg == 2:
                            assert not self.occupied, (
                                "must not replace an occupied diagnostic slot"
                            )
                            assert len(data) == 2616 and struct.unpack_from(
                                "<II", data
                            ) == (0xFFFFFFFF, 144)
                            assert struct.unpack_from("<III", data, 16) == (
                                144,
                                1,
                                14016,
                            )
                            assert data[32:40] == b"\xff" * 8
                            self.send(0xFFFFFFFF, 4)
                            self.send(h[1], 2, ack=True)
                            self.registered.set()
                        else:
                            raise AssertionError(f"unexpected native command {msg}")
                except (EOFError, ConnectionError, OSError):
                    pass
                finally:
                    self.client.close()
                    self.client = None
                    self.registered.clear()
        except BaseException as e:
            self.error = e

    def close(self):
        self.stop.set()
        self.thread.join(5)
        self.socket.close()
        if self.error:
            raise self.error
