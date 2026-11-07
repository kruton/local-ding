"""Exercise the ARM SSH server with isolated account and key storage."""

import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SSH(unittest.TestCase):
    def test_public_keys_and_terminal(self):
        qemu = os.environ.get(
            "LOCAL_DING_QEMU", shutil.which("qemu-arm") or "qemu-arm-static"
        )
        sysroot = os.environ.get("LOCAL_DING_SYSROOT")
        if not sysroot:
            self.skipTest("LOCAL_DING_SYSROOT is required")
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            runtime = base / "sysroot"
            etc = runtime / "etc"
            etc.mkdir(parents=True)
            shutil.copytree(Path(sysroot) / "lib", runtime / "lib", symlinks=True)
            home = base / "home"
            home.mkdir(mode=0o700)
            state = base / "state"
            state.mkdir(mode=0o700)
            (home / ".ssh").symlink_to(state)
            (etc / "passwd").write_text(
                f"localdingtest:x:{os.getuid()}:{os.getgid()}:test:{home}:/bin/sh\n"
            )
            (etc / "group").write_text(f"localdingtest:x:{os.getgid()}:\n")
            (etc / "nsswitch.conf").write_text("passwd: files\ngroup: files\n")
            (etc / "shells").write_text("/bin/sh\n")
            for name in ["identity", "unknown"]:
                subprocess.run(
                    [
                        "ssh-keygen",
                        "-q",
                        "-t",
                        "ed25519",
                        "-N",
                        "",
                        "-f",
                        str(base / name),
                    ],
                    check=True,
                )
            authorized = state / "authorized_keys"
            authorized.write_bytes((base / "identity.pub").read_bytes())
            authorized.chmod(0o600)
            binary = ROOT / "payload/dropbearmulti"
            arm = [qemu, "-cpu", "cortex-a9", "-L", str(runtime), str(binary)]
            hostkey = state / "hostkey"
            subprocess.run(
                arm + ["dropbearkey", "-t", "ed25519", "-f", str(hostkey)],
                check=True,
                capture_output=True,
            )
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            with (base / "server.log").open("w+") as log:
                server = subprocess.Popen(
                    arm
                    + [
                        "dropbear",
                        "-F",
                        "-p",
                        f"127.0.0.1:{port}",
                        "-r",
                        str(hostkey),
                        "-P",
                        str(base / "pid"),
                    ],
                    stdout=log,
                    stderr=log,
                )
                try:
                    for _ in range(100):
                        if server.poll() is not None:
                            log.seek(0)
                            self.fail(log.read())
                        try:
                            with socket.create_connection(
                                ("127.0.0.1", port), timeout=0.2
                            ) as sock:
                                self.assertIn(b"SSH-2.0-dropbear_", sock.recv(256))
                            break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        self.fail("SSH listener did not start")
                    ssh = [
                        "ssh",
                        "-F",
                        "/dev/null",
                        "-p",
                        str(port),
                        "-o",
                        "IdentitiesOnly=yes",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "StrictHostKeyChecking=no",
                        "-o",
                        "UserKnownHostsFile=/dev/null",
                        "-o",
                        "ConnectTimeout=5",
                    ]

                    def connect(identity, command, *options):
                        return subprocess.run(
                            ssh
                            + [
                                "-i",
                                str(base / identity),
                                *options,
                                "localdingtest@127.0.0.1",
                                command,
                            ],
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )

                    result = connect("identity", "printf ssh-command-ok")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "ssh-command-ok")
                    rejected = connect("unknown", "true")
                    self.assertEqual(rejected.returncode, 255, rejected.stderr)
                    self.assertIn("Permission denied (publickey)", rejected.stderr)
                    # Keep stdin open until the PTY command exits to avoid SIGHUP.
                    terminal = subprocess.Popen(
                        ssh
                        + [
                            "-tt",
                            "-i",
                            str(base / "identity"),
                            "localdingtest@127.0.0.1",
                            "test -t 0 && test -t 1 && tty && printf 'ssh-terminal-ok\\n'",
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    try:
                        terminal.wait(timeout=15)
                        output = terminal.stdout.read()
                        error = terminal.stderr.read()
                        self.assertEqual(terminal.returncode, 0, error)
                        self.assertIn("/dev/pts/", output)
                        self.assertIn("ssh-terminal-ok", output)
                    finally:
                        if terminal.poll() is None:
                            terminal.kill()
                            terminal.wait()
                        terminal.stdin.close()
                        terminal.stdout.close()
                        terminal.stderr.close()
                    # A webpage key update applies without restarting the daemon.
                    authorized.write_bytes((base / "unknown.pub").read_bytes())
                    self.assertEqual(connect("unknown", "true").returncode, 0)
                    self.assertEqual(connect("identity", "true").returncode, 255)
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()

    def test_startup_preserves_keys_and_rejects_unsafe_state(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for name in ["ram", "para", "app", "bin"]:
                (base / name).mkdir()
            mounts = base / "mounts"
            mounts.write_text(
                f"ubi {base}/para ubifs rw 0 0\ndevpts /dev/pts devpts rw 0 0\n"
            )
            for name in ["chown", "sync"]:
                stub = base / "bin" / name
                stub.write_text("#!/bin/sh\nexit 0\n")
                stub.chmod(0o755)
            for name, body in [
                ("dropbearkey", 'printf generated-host-key > "$4"'),
                ("dropbear", f'printf "%s\\n" "$@" > "{base}/arguments"'),
            ]:
                stub = base / "app" / name
                stub.write_text("#!/bin/sh\n" + body + "\n")
                stub.chmod(0o755)
            script = (ROOT / "assets/network/local-ding-ssh").read_text()
            for original, replacement in [
                ("/mnt/tmp", str(base / "ram")),
                ("/mnt/para", str(base / "para")),
                ("/mnt/app", str(base / "app")),
                ("/proc/mounts", str(mounts)),
            ]:
                script = script.replace(original, replacement)
            env = {**os.environ, "PATH": str(base / "bin") + ":" + os.environ["PATH"]}

            def start():
                return subprocess.run(
                    ["sh", "-c", script],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

            # No preinstalled owner key is needed; access is provisioned via web.
            result = start()
            self.assertEqual(result.returncode, 0, result.stderr)
            state = base / "para/dropbear"
            hostkey = state / "dropbear_ed25519_host_key"
            self.assertEqual(hostkey.read_text(), "generated-host-key")
            self.assertEqual(hostkey.stat().st_mode & 0o777, 0o600)
            self.assertEqual(state.stat().st_mode & 0o777, 0o700)
            self.assertIn("2222", (base / "arguments").read_text().splitlines())
            self.assertFalse((state / "authorized_keys").exists())
            authorized = state / "authorized_keys"
            authorized.write_text("owner-key\n")
            hostkey.write_text("persistent-host-key")
            self.assertEqual(start().returncode, 0)
            self.assertEqual(hostkey.read_text(), "persistent-host-key")
            self.assertEqual(authorized.read_text(), "owner-key\n")
            self.assertEqual(authorized.stat().st_mode & 0o777, 0o600)
            authorized.unlink()
            outside = base / "outside"
            outside.write_text("untouched")
            authorized.symlink_to(outside)
            self.assertNotEqual(start().returncode, 0)
            self.assertEqual(outside.read_text(), "untouched")
            authorized.unlink()
            mounts.write_text("")
            self.assertNotEqual(start().returncode, 0)
