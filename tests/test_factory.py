import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Factory(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("LOCAL_DING_FACTORY"), "Local factory input is not supplied"
    )
    def test_local_package(self):
        with tempfile.TemporaryDirectory() as t:
            subprocess.run(
                [
                    str(ROOT / "local-ding-fw"),
                    "patch",
                    "--factory",
                    os.environ["LOCAL_DING_FACTORY"],
                    "--build-version",
                    "7106_2611060005",
                    "--output-dir",
                    t,
                ],
                check=True,
            )
            p = next(Path(t).glob("*.pak"))
            subprocess.run(
                [
                    str(ROOT / "local-ding-fw"),
                    "verify",
                    str(p),
                    "--manifest",
                    str(p.with_suffix(".json")),
                ],
                check=True,
            )
