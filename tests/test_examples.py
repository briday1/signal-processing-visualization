import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from examples.advanced_gallery import GENERATORS


class AdvancedExampleTests(unittest.TestCase):
    def test_advanced_examples_are_finite_and_recover_simulated_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifests = {}
            for name, generator in GENERATORS.items():
                with self.subTest(example=name):
                    run = generator(root / name)
                    manifest = json.loads((run / "manifest.json").read_text())
                    manifests[name] = manifest
                    self.assertGreaterEqual(len(manifest["products"]), 6)
                    for product in manifest["products"]:
                        values = np.load(run / product["file"], mmap_mode="r")
                        self.assertEqual(list(values.shape), product["shape"])
                        self.assertTrue(np.all(np.isfinite(values)))
                        self.assertEqual(
                            set(product["coordinates"]), set(product["axes"])
                        )
                        if product["units"] == "binary":
                            self.assertTrue(set(np.unique(values)).issubset({0, 1}))

            gnss = manifests["gnss"]["metadata"]
            self.assertEqual(gnss["code_phase_error_chips"], 0)
            self.assertEqual(gnss["doppler_error_hz"], 0)

            self.assertLess(manifests["ct"]["metadata"]["reconstruction_rmse"], 0.12)

            ultrasound = manifests["ultrasound"]["metadata"]
            self.assertAlmostEqual(
                ultrasound["brightest_location_x_mm"], -3.0, delta=0.5
            )
            self.assertAlmostEqual(
                ultrasound["brightest_location_depth_mm"], 17.0, delta=0.5
            )

            channelizer_run = root / "channelizer"
            channelizer = manifests["channelizer"]
            occupancy_product = next(
                product
                for product in channelizer["products"]
                if product["name"] == "Channel occupancy"
            )
            occupancy = np.load(channelizer_run / occupancy_product["file"])
            coordinate_file = occupancy_product["coordinates"]["channel center"]["file"]
            frequencies = np.load(channelizer_run / coordinate_file)
            strongest = set(np.round(frequencies[np.argsort(occupancy)[-2:]], 1))
            self.assertEqual(strongest, {-187.5, 125.0})


if __name__ == "__main__":
    unittest.main()
