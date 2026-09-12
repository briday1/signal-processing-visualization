import json
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

import spviz
from spviz.observer import Recorder
from spviz.server import RunStore


class SessionTests(unittest.TestCase):
    def test_runtime_and_package_versions_match(self):
        project = (Path(__file__).parents[1] / "pyproject.toml").read_text()
        version = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)
        self.assertIsNotNone(version)
        self.assertEqual(spviz.__version__, version.group(1))

    def test_capture_representation_controls_display_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            with spviz.Session(Path(directory) / "run") as run:
                product_id = run.capture(
                    "iq",
                    np.array([1 + 2j, -3 + 4j]),
                    representation="real",
                )
                with self.assertRaisesRegex(ValueError, "requires complex"):
                    run.capture("invalid", np.arange(4), representation="phase")
                with self.assertRaisesRegex(ValueError, "representation must"):
                    run.capture("invalid", np.arange(4), representation="rainbow")
            manifest = json.loads(
                (Path(directory) / "run" / "manifest.json").read_text()
            )
            product = next(
                item for item in manifest["products"] if item["id"] == product_id
            )
            self.assertEqual(product["representation"], "real")
            self.assertEqual(product["stats"]["display_min"], -3.0)
            self.assertEqual(product["stats"]["display_max"], 1.0)

    def test_statistics_handle_numeric_extremes_and_offer_bounded_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            with spviz.Session(Path(directory) / "run") as run:
                run.capture(
                    "integer magnitude",
                    np.array([np.iinfo(np.int64).min], dtype=np.int64),
                    representation="magnitude",
                )
                run.capture("large mean", np.array([1e308, 1e308]))
                sampled = np.zeros((2000, 2000), dtype=np.float32)
                sampled[999, 999] = 99
                run.capture("sampled", sampled, statistics="sampled")
                run.capture("unstatted", sampled, statistics="none", vmin=0, vmax=1)
                with self.assertRaisesRegex(ValueError, "requires both vmin and vmax"):
                    run.capture("missing range", sampled, statistics="none")
                with self.assertRaisesRegex(ValueError, "overflows"):
                    run.capture(
                        "overflowing power",
                        np.array([1e308]),
                        representation="power",
                    )
            products = json.loads(
                (Path(directory) / "run" / "manifest.json").read_text()
            )["products"]
            self.assertEqual(products[0]["stats"]["display_max"], float(2**63))
            self.assertEqual(products[1]["stats"]["display_mean"], 1e308)
            self.assertEqual(products[2]["statistics"], "sampled")
            self.assertEqual(products[2]["stats"]["display_max"], 0)
            self.assertEqual(products[3]["statistics"], "none")
            self.assertIsNone(products[3]["stats"]["display_max"])

    def test_capture_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            source = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
            with spviz.Session(run_path, name="test") as run:
                product_id = run.capture(
                    "IQ data", source, axes=["channel", "pulse", "sample"]
                )
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual(manifest["products"][0]["id"], product_id)
            self.assertEqual(manifest["products"][0]["shape"], [2, 3, 4])
            np.testing.assert_array_equal(
                np.load(run_path / "arrays" / "iq-data.npy"), source
            )

    def test_slice_permutation_and_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            source = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
            with spviz.Session(run_path) as run:
                product_id = run.capture("cube", source, axes=["a", "b", "c"])
            result = RunStore(run_path).slice(product_id, [2, 0, 1], 1)
            self.assertEqual(result["shape"], [4, 2, 3])
            self.assertEqual(
                result["values"], np.transpose(source, [2, 0, 1])[1].tolist()
            )
            metadata, body = RunStore(run_path).slice_binary(product_id, [2, 0, 1], 1)
            self.assertEqual((metadata["rows"], metadata["columns"]), (2, 3))
            np.testing.assert_array_equal(
                np.frombuffer(body, dtype="<f4").reshape(2, 3),
                np.transpose(source, [2, 0, 1])[1],
            )

    def test_slice_quality_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            source = np.arange(2 * 200 * 300, dtype=np.float32).reshape(2, 200, 300)
            with spviz.Session(run_path) as run:
                product_id = run.capture("large", source, axes=["layer", "y", "x"])
            low, _ = RunStore(run_path).slice_binary(product_id, [0, 1, 2], 0, limit=32)
            high, _ = RunStore(run_path).slice_binary(
                product_id, [0, 1, 2], 0, limit=192
            )
            self.assertEqual((low["rows"], low["columns"]), (32, 32))
            self.assertEqual((high["rows"], high["columns"]), (192, 192))
            self.assertGreater(
                high["rows"] * high["columns"], low["rows"] * low["columns"]
            )

    def test_axis_validation(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            spviz.Session(Path(directory) / "run") as run,
            self.assertRaisesRegex(ValueError, "axis names"),
        ):
            run.capture("bad", np.zeros((2, 2)), axes=["only one"])

    def test_higher_dimensions_require_named_view_axes(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            source = np.arange(120, dtype=np.float32).reshape(2, 3, 4, 5)
            with spviz.Session(run_path) as run:
                with self.assertRaisesRegex(ValueError, "require view_axes"):
                    run.capture(
                        "four-d", source, axes=["frame", "beam", "doppler", "range"]
                    )
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
            self.assertIs(
                recorder.tap(source, "source", axes=["channel", "sample"]), source
            )
            output = source * 2
            self.assertIs(
                recorder.tap(
                    output,
                    "output",
                    axes=["channel", "sample"],
                    operation="gain",
                    inputs=source,
                ),
                output,
            )
            recorder.close()
            manifest = json.loads(
                (Path(directory) / "run" / "manifest.json").read_text()
            )
            self.assertEqual(
                manifest["products"][1]["upstream"], [manifest["products"][0]["id"]]
            )

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
            self.assertEqual(
                store.coordinates(product_id, 0)["values"], ["left", "right"]
            )
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
                recorder.tap(
                    value * 2, "Another product", filename="receiver_input.npy"
                )
            with self.assertRaisesRegex(ValueError, "not a path"):
                recorder.tap(value * 3, "Unsafe", filename="nested/value.npy")
            recorder.close()
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual(
                manifest["products"][0]["file"], "arrays/receiver_input.npy"
            )
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
            self.assertEqual(
                [product["scale"] for product in manifest["products"]],
                ["linear", "log"],
            )

    def test_product_overview_aspect(self):
        with tempfile.TemporaryDirectory() as directory:
            with spviz.Session(Path(directory) / "run") as run:
                run.capture("fit", np.ones((2, 8)), overview_aspect="fit")
                with self.assertRaisesRegex(ValueError, "overview_aspect must"):
                    run.capture("bad", np.ones((2, 8)), overview_aspect="wide")
            manifest = json.loads(
                (Path(directory) / "run" / "manifest.json").read_text()
            )
            self.assertEqual(manifest["products"][0]["overview_aspect"], "fit")

    def test_product_display_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                run.capture("limited", np.arange(8), vmin=2.5, vmax=6.5)
                with self.assertRaisesRegex(ValueError, "vmin must be less"):
                    run.capture("bad", np.arange(8), vmin=7, vmax=2)
            product = json.loads((run_path / "manifest.json").read_text())["products"][
                0
            ]
            self.assertEqual(
                (product["display_min"], product["display_max"]), (2.5, 6.5)
            )

    def test_public_capture_uses_active_session(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path):
                product_id = spviz.capture("public", np.arange(3))
            self.assertEqual(product_id, "public")
            self.assertIn("capture", spviz.__all__)

    def test_capture_rejects_empty_and_non_numeric_data_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                with self.assertRaisesRegex(ValueError, "cannot be empty"):
                    run.capture("empty", np.empty((2, 0)))
                with self.assertRaisesRegex(ValueError, "numeric or boolean"):
                    run.capture("text", np.array(["not", "numeric"]))
                run.capture("boolean", np.array([True, False]))
            self.assertFalse((run_path / "arrays" / "empty.npy").exists())
            self.assertFalse((run_path / "arrays" / "text.npy").exists())
            self.assertTrue((run_path / "arrays" / "boolean.npy").exists())

    def test_invalid_metadata_and_coordinates_leave_no_product_files(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                with self.assertRaisesRegex(ValueError, "product metadata"):
                    run.capture(
                        "object metadata", np.ones(2), metadata={"bad": object()}
                    )
                with self.assertRaisesRegex(ValueError, "product metadata"):
                    run.capture("nan metadata", np.ones(2), metadata={"bad": np.nan})
                with self.assertRaisesRegex(ValueError, "must be one-dimensional"):
                    run.capture("bad coordinates", np.ones(2), coordinates={0: [1]})
                recursive: list[object] = []
                recursive.append(recursive)
                with self.assertRaisesRegex(ValueError, "product metadata"):
                    run.capture("recursive", np.ones(2), metadata={"bad": recursive})
                structured = np.array([(1, 2)], dtype=[("a", "i4"), ("b", "i4")])
                with self.assertRaisesRegex(ValueError, "scalar labels"):
                    run.capture(
                        "structured coordinates",
                        np.ones(1),
                        coordinates={0: structured},
                    )
            self.assertEqual(list((run_path / "arrays").iterdir()), [])

    def test_generated_and_explicit_filenames_are_portable_in_length(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                product_id = run.capture("very long " * 100, np.ones(1))
                self.assertLessEqual(len(product_id), 96)
                with self.assertRaisesRegex(ValueError, "too long"):
                    run.capture("explicit", np.ones(1), filename=f"{'x' * 201}.npy")

    def test_mutated_session_metadata_is_validated_before_manifest_write(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            run = spviz.Session(run_path, metadata={"valid": True})
            run.capture("data", np.ones(2))
            run.metadata["invalid"] = float("nan")
            with self.assertRaisesRegex(ValueError, "session metadata"):
                run.close()
            self.assertFalse((run_path / "manifest.json").exists())

    def test_implicit_and_coordinate_names_never_overwrite_reserved_files(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                run.capture("first", np.array([1]), filename="second.npy")
                run.capture("Second", np.array([2]))
                run.capture(
                    "reserve coordinate", np.array([3]), filename="data--axis-0.npy"
                )
                run.capture(
                    "Data", np.array([4]), axes=["sample"], coordinates={"sample": [10]}
                )
            products = json.loads((run_path / "manifest.json").read_text())["products"]
            files = [product["file"] for product in products]
            coordinate_file = products[-1]["coordinates"]["sample"]["file"]
            self.assertEqual(len({*files, coordinate_file}), len(files) + 1)
            np.testing.assert_array_equal(
                np.load(run_path / "arrays" / "second.npy"), [1]
            )
            np.testing.assert_array_equal(np.load(run_path / products[1]["file"]), [2])
            np.testing.assert_array_equal(np.load(run_path / coordinate_file), [10])

    def test_upstream_ids_are_validated_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                source = run.capture("source", np.ones(2))
                output = run.capture("output", np.ones(2), upstream=[source, source])
                with self.assertRaisesRegex(ValueError, "Unknown upstream product"):
                    run.capture("bad", np.ones(2), upstream="missing")
            products = json.loads((run_path / "manifest.json").read_text())["products"]
            self.assertEqual(products[1]["id"], output)
            self.assertEqual(products[1]["upstream"], [source])
            self.assertFalse((run_path / "arrays" / "bad.npy").exists())

    def test_recorder_accepts_iterable_objects_and_explicit_product_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            recorder = Recorder(run_path)
            first = recorder.tap(np.ones(2), "first")
            second = recorder.tap(np.ones(2), "second")
            recorder.tap(
                np.ones(2),
                "joined",
                inputs=(value for value in (first, second, "first")),
            )
            recorder.close()
            product = json.loads((run_path / "manifest.json").read_text())["products"][
                -1
            ]
            self.assertEqual(product["upstream"], ["first", "second"])

    def test_concurrent_session_capture_produces_unique_complete_products(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            run = spviz.Session(run_path)
            with ThreadPoolExecutor(max_workers=6) as executor:
                product_ids = list(
                    executor.map(
                        lambda _: run.capture("sample", np.arange(8)), range(20)
                    )
                )
            run.close()
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual(len(set(product_ids)), 20)
            self.assertEqual(len(manifest["products"]), 20)
            self.assertEqual(len(list((run_path / "arrays").glob("*.npy"))), 20)
            self.assertEqual(list((run_path / "arrays").glob(".*.tmp")), [])

    def test_replacement_is_transactional_and_removes_old_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                run.capture("old", np.array([1]))
            old_manifest = (run_path / "manifest.json").read_bytes()

            with (
                self.assertRaisesRegex(RuntimeError, "pipeline failed"),
                spviz.Session(run_path) as run,
            ):
                run.capture("unfinished", np.array([2]))
                raise RuntimeError("pipeline failed")
            self.assertEqual((run_path / "manifest.json").read_bytes(), old_manifest)
            self.assertTrue((run_path / "arrays" / "old.npy").is_file())

            with spviz.Session(run_path) as run:
                run.capture("new", np.array([3]))
            self.assertFalse((run_path / "arrays" / "old.npy").exists())
            self.assertTrue((run_path / "arrays" / "new.npy").is_file())

    def test_session_modes_and_concurrent_directory_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                run.capture("original", np.array([1]))
            with self.assertRaises(FileExistsError):
                spviz.Session(run_path, mode="error")

            first = spviz.Session(run_path)
            second = spviz.Session(run_path)
            first.capture("first", np.array([2]))
            second.capture("second", np.array([3]))
            first.close()
            with self.assertRaisesRegex(RuntimeError, "changed while"):
                second.close()
            second.abort()
            manifest = json.loads((run_path / "manifest.json").read_text())
            self.assertEqual([item["name"] for item in manifest["products"]], ["first"])

            unrelated = Path(directory) / "unrelated"
            unrelated.mkdir()
            (unrelated / "notes.txt").write_text("keep")
            with self.assertRaisesRegex(ValueError, "not an spviz run"):
                spviz.Session(unrelated)


if __name__ == "__main__":
    unittest.main()
