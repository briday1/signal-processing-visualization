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

    def test_preview_color_transfer_matches_browser(self):
        import shutil
        import subprocess

        from spviz.previews import display_rgba

        if not shutil.which("node"):
            self.skipTest("Node.js required for cross-renderer parity")
        fixtures = [
            {
                "values": [0, 0.01, 0.1, 0.5, 1, None],
                "low": 0,
                "high": 1,
                "phase": False,
                "log": False,
                "binary": False,
            },
            {
                "values": [-np.pi, -2, -0.5, 0, 0.5, 2, np.pi],
                "low": -np.pi,
                "high": np.pi,
                "phase": True,
                "log": False,
                "binary": False,
            },
            {
                "values": [-2, -0.5, 0, 0.5, 3, 9],
                "low": -2,
                "high": 9,
                "phase": False,
                "log": False,
                "binary": False,
            },
            {
                "values": [0, 0.001, 0.01, 1, 5, 10],
                "low": 0.001,
                "high": 10,
                "phase": False,
                "log": True,
                "binary": False,
            },
            {
                "values": [0, 1],
                "low": 0,
                "high": 1,
                "phase": False,
                "log": False,
                "binary": True,
            },
        ]
        result = subprocess.run(
            ["node", str(Path(__file__).with_name("web-preview-colors.cjs"))],
            input=json.dumps(fixtures),
            text=True,
            capture_output=True,
            check=True,
        )
        for fixture, expected in zip(fixtures, json.loads(result.stdout)):
            values = np.array(fixture.pop("values"), dtype=np.float32)
            rgba = display_rgba(values, **fixture).reshape(-1)
            np.testing.assert_allclose(rgba, expected, atol=1, rtol=0)

    def test_static_preview_uses_exported_first_slice_when_budget_caps_density(self):
        from spviz.previews import preview_png

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            values = np.arange(3 * 101 * 137, dtype=np.float32).reshape(3, 101, 137)
            with spviz.Session(root / "run") as run:
                product = run.capture("cube", values, axes=["a", "b", "c"])
            with patch("spviz.static.preview_png", wraps=preview_png) as renderer:
                site = export_static(
                    root / "run", root / "site", max_volume_bytes=20000
                )
            selected = renderer.call_args.kwargs["selected"]
            meta = json.loads(
                (site / "data/volumes" / f"{product}--0-1-2.json").read_text()
            )
            self.assertTrue(meta["static_export"]["plane_capped"])
            expected = values[0][np.ix_(meta["row_indices"], meta["column_indices"])]
            np.testing.assert_array_equal(selected, expected)
            sampled_meta = renderer.call_args.kwargs["sampled"][0]
            self.assertEqual(sampled_meta["depth_indices"], [0, 1, 2])
