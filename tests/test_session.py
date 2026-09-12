import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import spviz
from spviz.server import RunStore
from spviz.observer import Recorder


class SessionTests(unittest.TestCase):
    def test_capture_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            source = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
            with spviz.Session(run_path, name="test") as run:
                product_id = run.capture("IQ data", source, axes=["channel", "pulse", "sample"])
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual(manifest["products"][0]["id"], product_id)
            self.assertEqual(manifest["products"][0]["shape"], [2, 3, 4])
            np.testing.assert_array_equal(np.load(run_path / "arrays" / "iq-data.npy"), source)

    def test_slice_permutation_and_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            source = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
            with spviz.Session(run_path) as run:
                product_id = run.capture("cube", source, axes=["a", "b", "c"])
            result = RunStore(run_path).slice(product_id, [2, 0, 1], 1)
            self.assertEqual(result["shape"], [4, 2, 3])
            self.assertEqual(result["values"], np.transpose(source, [2, 0, 1])[1].tolist())
            metadata, body = RunStore(run_path).slice_binary(product_id, [2, 0, 1], 1)
            self.assertEqual((metadata["rows"], metadata["columns"]), (2, 3))
            np.testing.assert_array_equal(np.frombuffer(body, dtype="<f4").reshape(2, 3), np.transpose(source, [2, 0, 1])[1])

    def test_slice_quality_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            source = np.arange(2 * 200 * 300, dtype=np.float32).reshape(2, 200, 300)
            with spviz.Session(run_path) as run:
                product_id = run.capture("large", source, axes=["layer", "y", "x"])
            low, _ = RunStore(run_path).slice_binary(product_id, [0, 1, 2], 0, limit=32)
            high, _ = RunStore(run_path).slice_binary(product_id, [0, 1, 2], 0, limit=192)
            self.assertEqual((low["rows"], low["columns"]), (32, 32))
            self.assertEqual((high["rows"], high["columns"]), (192, 192))
            self.assertGreater(high["rows"] * high["columns"], low["rows"] * low["columns"])

    def test_axis_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            with spviz.Session(Path(directory) / "run") as run:
                with self.assertRaisesRegex(ValueError, "axis names"):
                    run.capture("bad", np.zeros((2, 2)), axes=["only one"])

    def test_higher_dimensions_require_named_view_axes(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            source = np.arange(120, dtype=np.float32).reshape(2, 3, 4, 5)
            with spviz.Session(run_path) as run:
                with self.assertRaisesRegex(ValueError, "require view_axes"):
                    run.capture("four-d", source, axes=["frame", "beam", "doppler", "range"])
                product_id = run.capture(
                    "four-d",
                    source,
                    axes=["frame", "beam", "doppler", "range"],
                    view_axes=["beam", "doppler", "range"],
                )
            store = RunStore(run_path)
            result = store.slice(product_id, [1, 2, 3], 2, indices={0: 1})
            self.assertEqual(result["shape"], [3, 4, 5])
            self.assertEqual(result["values"], source[1, 2].tolist())

    def test_tap_preserves_identity_and_infers_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = Recorder(Path(directory) / "run")
            source = np.ones((2, 4), dtype=np.complex64)
            self.assertIs(recorder.tap(source, "source", axes=["channel", "sample"]), source)
            output = source * 2
            self.assertIs(
                recorder.tap(output, "output", axes=["channel", "sample"], operation="gain", inputs=source),
                output,
            )
            recorder.close()
            manifest = json.loads((Path(directory) / "run" / "manifest.json").read_text())
            self.assertEqual(manifest["products"][1]["upstream"], [manifest["products"][0]["id"]])

    def test_named_axis_coordinates_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                product_id = run.capture(
                    "spectrum",
                    np.ones((2, 3)),
                    axes=["channel", "frequency"],
                    coordinates={
                        "channel": ["left", "right"],
                        "frequency": {"values": [100.0, 200.0, 300.0], "units": "Hz"},
                    },
                )
            store = RunStore(run_path)
            self.assertEqual(store.coordinates(product_id, 0)["values"], ["left", "right"])
            frequency = store.coordinates(product_id, 1)
            self.assertEqual(frequency["values"], [100.0, 200.0, 300.0])
            self.assertEqual(frequency["units"], "Hz")

    def test_explicit_product_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            recorder = Recorder(run_path)
            value = np.ones((2, 3))
            recorder.tap(value, "Display name", filename="receiver_input.npy")
            with self.assertRaisesRegex(ValueError, "already used"):
                recorder.tap(value * 2, "Another product", filename="receiver_input.npy")
            with self.assertRaisesRegex(ValueError, "not a path"):
                recorder.tap(value * 3, "Unsafe", filename="nested/value.npy")
            recorder.close()
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual(manifest["products"][0]["file"], "arrays/receiver_input.npy")
            self.assertTrue((run_path / "arrays" / "receiver_input.npy").is_file())

    def test_product_display_scale(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                run.capture("linear", np.ones((2, 2)))
                run.capture("log", np.ones((2, 2)), scale="log")
                with self.assertRaisesRegex(ValueError, "scale must"):
                    run.capture("bad", np.ones((2, 2)), scale="decibels")
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual([product["scale"] for product in manifest["products"]], ["linear", "log"])

    def test_product_overview_aspect(self):
        with tempfile.TemporaryDirectory() as directory:
            with spviz.Session(Path(directory) / "run") as run:
                run.capture("fit", np.ones((2, 8)), overview_aspect="fit")
                with self.assertRaisesRegex(ValueError, "overview_aspect must"):
                    run.capture("bad", np.ones((2, 8)), overview_aspect="wide")
            manifest = json.loads((Path(directory) / "run" / "manifest.json").read_text())
            self.assertEqual(manifest["products"][0]["overview_aspect"], "fit")

    def test_product_display_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                run.capture("limited", np.arange(8), vmin=2.5, vmax=6.5)
                with self.assertRaisesRegex(ValueError, "vmin must be less"):
                    run.capture("bad", np.arange(8), vmin=7, vmax=2)
            product = json.loads((run_path / "manifest.json").read_text())["products"][0]
            self.assertEqual((product["display_min"], product["display_max"]), (2.5, 6.5))


if __name__ == "__main__":
    unittest.main()
