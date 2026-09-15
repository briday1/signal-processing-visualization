import shutil
import subprocess
import unittest
from pathlib import Path


class CarouselTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for viewer tests")
    def test_carousel_interactions(self):
        subprocess.run(
            ["node", str(Path(__file__).with_name("web-carousel.test.cjs"))],
            check=True,
            capture_output=True,
            text=True,
        )
