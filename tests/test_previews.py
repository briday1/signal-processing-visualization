import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import spviz
from spviz.server import RunStore
from spviz.static import export_static


class PreviewTests(unittest.TestCase):
    def test_png_is_high_resolution_and_reused_without_reading_array(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with spviz.Session(root / "run") as run:
                product = run.capture(
                    "IQ",
                    np.ones((4, 32, 64), dtype=np.complex64),
                    axes=["a", "b", "c"],
                    views={
                        "Amplitude": {"representation": "magnitude"},
                        "Phase": {"representation": "phase"},
                    },
                )
            store = RunStore(root / "run")
            key, png = store.preview(product)
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack("!II", png[16:24]), (640, 480))
            reopened = RunStore(root / "run")
            with patch.object(
                reopened,
                "volume_binary",
                side_effect=AssertionError("must use saved PNG"),
            ):
                self.assertEqual(reopened.preview(product), (key, png))
            phase = store.manifest["products"][1]["id"]
            self.assertNotEqual(store.preview(phase)[0], key)
            store.products[product]["display_max"] = 2
            self.assertNotEqual(store.preview(product)[0], key)
            source = store._run_file(store.products[product]["file"], product=product)
            np.save(source, np.full((4, 32, 64), 3, dtype=np.complex64))
            self.assertNotEqual(store.preview(product)[0], key)

    def test_large_static_volume_uses_exact_layer_chunks_and_small_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = np.arange(16 * 128 * 256, dtype=np.float32).reshape(16, 128, 256)
            with spviz.Session(root / "run") as run:
                product = run.capture(
                    "cube", data, axes=["receiver", "pulse", "sample"]
                )
            site = export_static(root / "run", root / "site") / "data"
            stem = f"{product}--0-1-2"
            metadata = json.loads((site / "volumes" / f"{stem}.json").read_text())
            self.assertTrue(metadata["layer_chunks"])
            self.assertFalse((site / "volumes" / f"{stem}.f32").exists())
            layer = np.fromfile(
                site / "layers" / f"{stem}--7.f32", dtype="<f4"
            ).reshape(128, 256)
            np.testing.assert_array_equal(layer, data[7])
            self.assertLess(
                (site / "contexts" / f"{stem}.f32").stat().st_size, data.nbytes / 4
            )
            self.assertTrue((site / "previews" / f"{product}.png").is_file())
            for meta_file in (site / "volumes").glob("*.json"):
                meta = json.loads(meta_file.read_text())
                chunks = list((site / "layers").glob(f"{meta_file.stem}--*.f32"))
                self.assertEqual(
                    sum(chunk.stat().st_size for chunk in chunks),
                    meta["static_export"]["payload_bytes"],
                )
