import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AssetSyntax(unittest.TestCase):
    def test_shell_syntax(self):
        for path in (ROOT / "assets").rglob("*"):
            if path.is_file() and path.read_bytes().startswith(b"#!/bin/sh\n"):
                subprocess.run(["sh", "-n", str(path)], check=True)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_javascript_syntax(self):
        subprocess.run(
            ["node", "--check", str(ROOT / "assets/settings/settings.js")], check=True
        )

    def test_web_renderer(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory)
            for name, text in {
                "header.conf": "include mime.types;\n",
                "rtmp.conf": "@RTMP_BIND@@RTMP_PORT@\n",
                "http.conf": "@HTTP_PORT@ @HTTP_FLAG@ @HTTP_REMOTE_FLAG@\n",
                "https.conf": "@HTTPS_PORT@ @TLS_CERT@ @TLS_KEY@\n",
            }.items():
                (assets / name).write_text(text)
            for http, https, rtmp, flag in [
                (1, 1, 0, 111),
                (0, 1, 1, 101),
                (0, 0, 0, 100),
            ]:
                fixture = f'<service><web port="8080" https_port="8443" enable_http="{http}" enable_https="{https}" enable_rtmp="{rtmp}" /></service>'
                result = subprocess.run(
                    [
                        "awk",
                        "-v",
                        f"assets={assets}",
                        "-f",
                        str(ROOT / "assets/network/render-web.awk"),
                    ],
                    input=fixture,
                    text=True,
                    capture_output=True,
                    check=True,
                ).stdout
                expected = "include /mnt/tmp/mime.types;\n"
                expected += ("" if rtmp else "127.0.0.1:") + "1935\n"
                expected += f"8080 {flag} {flag * 10 + 1}\n"
                if https:
                    expected += "8443 /mnt/app/nginx_conf/self.crt /mnt/app/nginx_conf/self.key\n"
                self.assertEqual(result, expected + "}\n")
            bad = subprocess.run(
                [
                    "awk",
                    "-v",
                    f"assets={assets}",
                    "-f",
                    str(ROOT / "assets/network/render-web.awk"),
                ],
                input='<web port="invalid" />',
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(bad.returncode, 0)
