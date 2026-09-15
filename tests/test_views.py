import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import spviz
from examples.interferometry import generate
from spviz.server import RunStore
from spviz.static import export_static


class ViewsTests(unittest.TestCase):
    def test_views_share_capture_and_export_distinct_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run"
            value = np.array([1j, -2j, -3 + 0j])
            recorder = spviz.Recorder(path)
            self.assertIs(
                recorder.tap(
                    value,
                    "IQ",
                    views={
                        "Amplitude": {"representation": "magnitude"},
                        "Phase": {"representation": "phase"},
                    },
                ),
                value,
            )
            recorder.tap(value.real, "Output", inputs=value)
            recorder.close()
            manifest = json.loads((path / "manifest.json").read_text())
            self.assertEqual(len(manifest["products"]), 2)
            self.assertEqual(len(list((path / "arrays").glob("*.npy"))), 2)
            store = RunStore(path)
            amplitude, phase, output = store.manifest["products"]
            self.assertEqual(amplitude["file"], phase["file"])
            self.assertEqual(output["upstream"], [amplitude["id"]])
            self.assertEqual(phase["units"], "rad")
            self.assertAlmostEqual(phase["display_min"], -np.pi)
            for product, expected in [
                (amplitude, np.abs(value)),
                (phase, np.angle(value)),
            ]:
                _, body = store.volume_binary(product["id"], [0], 100)
                np.testing.assert_allclose(
                    np.frombuffer(body, dtype="<f4"), expected, atol=1e-6
                )
            site = export_static(path, Path(directory) / "site")
            exported = json.loads((site / "data/run.json").read_text())
            self.assertEqual(len(exported["products"]), 3)
            self.assertEqual(exported["capture_count"], 2)
            for product, expected in [
                (amplitude, np.abs(value)),
                (phase, np.angle(value)),
            ]:
                data = np.fromfile(
                    site / f"data/volumes/{product['id']}--0.f32", dtype="<f4"
                )
                np.testing.assert_allclose(data, expected, atol=1e-6)

    def test_invalid_views_fail_before_array_write(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            spviz.Session(Path(directory) / "run") as session,
        ):
            for views in [
                {},
                {"": {}},
                {"Phase": {"representation": "phase"}},
                {"A": {"vmin": 3, "vmax": 1}},
                {"A": {"filename": "bad.npy"}},
                {"A": {"view_axes": ["missing"]}},
            ]:
                with self.subTest(views=views), self.assertRaises(ValueError):
                    session.capture("Invalid", np.ones(4), views=views)
            self.assertEqual(session.products, [])
            self.assertEqual(list((session._storage_path / "arrays").iterdir()), [])

    def test_instrument_forwards_views_and_preserves_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = spviz.init(Path(directory) / "run")

            @spviz.instrument(
                views={
                    "Real": {"representation": "real"},
                    "Imaginary": {"representation": "imag"},
                }
            )
            def transform(value):
                return value * 1j

            source = spviz.tap(np.ones(3, dtype=complex), "source")
            transformed = transform(source)
            self.assertEqual(recorder.product_for(transformed), "transform")
            recorder.close()
            store = RunStore(recorder.path)
            self.assertEqual(len(store.products), 3)
            self.assertEqual(store.products["transform"]["upstream"], ["source"])

    def test_interferometry_recovers_angle_and_visibility_phase(self):
        with tempfile.TemporaryDirectory() as directory:
            path = generate(Path(directory) / "interferometry")
            manifest = json.loads((path / "manifest.json").read_text())
            self.assertAlmostEqual(
                manifest["metadata"]["brightest_angle_deg"], -18, delta=0.5
            )
            raw, cross, visibility = manifest["products"][:3]
            x = np.load(path / raw["file"])
            v = np.load(path / visibility["file"])
            np.testing.assert_allclose(v, np.load(path / cross["file"]).mean(axis=-1))
            positions = np.load(path / raw["coordinates"]["receiver"]["file"])
            i, j = np.tril_indices(len(positions), -1)
            order = np.argsort(positions[i] - positions[j])
            np.testing.assert_allclose(
                v, (x[i[order]] * x[j[order]].conj()).mean(axis=-1)
            )
            export_static(path, Path(directory) / "site", max_volume_bytes=100_000)
