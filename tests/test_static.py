import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import spviz
from spviz.static import export_static


class StaticExportTests(unittest.TestCase):
    def test_export_contains_browser_assets_and_all_permutations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with spviz.Session(root / "run") as run:
                run.capture("cube", np.ones((2, 3, 4)), axes=["a", "b", "c"])
            site = export_static(root / "run", root / "site")

            self.assertTrue((site / "index.html").is_file())
            self.assertIn('option value="viridis"', (site / "index.html").read_text())
            self.assertIn("SPVIZ_STATIC_BASE", (site / "config.js").read_text())
            self.assertIn("SPVIZ_GALLERY_URL", (site / "config.js").read_text())
            manifest = json.loads((site / "data" / "run.json").read_text())
            product_id = manifest["products"][0]["id"]
            volumes = list((site / "data" / "volumes").glob(f"{product_id}--*.f32"))
            self.assertEqual(len(volumes), 6)


if __name__ == "__main__":
    unittest.main()
