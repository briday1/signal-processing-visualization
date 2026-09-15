import http.client
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

import spviz
from spviz.server import RunStore, make_handler


class ServerTests(unittest.TestCase):
    def test_one_dimensional_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                product_id = run.capture("trace", np.arange(-8, 9), axes=["time"])
            metadata, body = RunStore(run_path).volume_binary(product_id, [0], limit=12)
            self.assertEqual(metadata["shape"], [1, 1, 12])
            values = np.frombuffer(body, dtype="<f4")
            self.assertEqual(values.size, 12)
            self.assertLess(values.min(), 0)

    def test_signed_multidimensional_values_are_not_silently_magnitudes(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            values = np.arange(-12, 12, dtype=np.float32).reshape(2, 3, 4)
            with spviz.Session(run_path) as run:
                product_id = run.capture("signed", values, axes=["a", "b", "c"])
            store = RunStore(run_path)
            _, body = store.volume_binary(product_id, [0, 1, 2], limit=8)
            rendered = np.frombuffer(body, dtype="<f4").reshape(values.shape)
            np.testing.assert_array_equal(rendered, values)

    def test_complex_representations_are_explicit_and_correct(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            values = np.array([[1 + 2j, -3 + 4j]], dtype=np.complex64)
            with spviz.Session(run_path) as run:
                product_id = run.capture(
                    "complex", values, axes=["row", "sample"], representation="real"
                )
            store = RunStore(run_path)
            _, real_body = store.volume_binary(product_id, [0, 1], limit=8)
            _, phase_body = store.volume_binary(
                product_id, [0, 1], limit=8, representation="phase"
            )
            np.testing.assert_allclose(np.frombuffer(real_body, dtype="<f4"), [1, -3])
            np.testing.assert_allclose(
                np.frombuffer(phase_body, dtype="<f4"), np.angle(values).ravel()
            )

    def test_volume_is_bounded_and_reports_exact_source_indices(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            values = np.arange(101 * 53 * 71, dtype=np.float32).reshape(101, 53, 71)
            with spviz.Session(run_path) as run:
                product_id = run.capture("large", values, axes=["a", "b", "c"])
            metadata, body = RunStore(run_path).volume_binary(
                product_id, [0, 1, 2], limit=17, depth_limit=9
            )
            self.assertEqual(metadata["shape"], [9, 17, 17])
            self.assertEqual(len(metadata["depth_indices"]), 9)
            self.assertEqual(len(metadata["row_indices"]), 17)
            self.assertEqual(len(metadata["column_indices"]), 17)
            sampled = np.frombuffer(body, dtype="<f4").reshape(metadata["shape"])
            expected = values[
                np.ix_(
                    metadata["depth_indices"],
                    metadata["row_indices"],
                    metadata["column_indices"],
                )
            ]
            np.testing.assert_array_equal(sampled, expected)

    def test_linear_coordinates_use_compact_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            coordinate = np.linspace(-4.0, 9.0, 100_000)
            with spviz.Session(run_path) as run:
                product_id = run.capture(
                    "trace",
                    np.ones(len(coordinate)),
                    axes=["time"],
                    coordinates={"time": {"values": coordinate, "units": "s"}},
                )
            payload = RunStore(run_path).coordinates(product_id, 0)
            self.assertEqual(payload["encoding"], "linear")
            self.assertEqual(payload["length"], 100_000)
            self.assertNotIn("values", payload)

    def test_http_api(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                product_id = run.capture(
                    "data", np.ones((2, 3, 4)), axes=["a", "b", "c"]
                )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), make_handler(RunStore(run_path))
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(base + "/api/run") as response:
                    manifest = json.load(response)
                with urllib.request.urlopen(
                    base + f"/api/product/{product_id}/slice?perm=0,1,2&layer=1"
                ) as response:
                    payload = json.load(response)
                with urllib.request.urlopen(
                    base + f"/api/product/{product_id}/volume?perm=0,1,2&limit=4"
                ) as response:
                    volume_metadata = json.loads(response.headers["X-Spviz-Metadata"])
                    volume = np.frombuffer(response.read(), dtype="<f4")
                self.assertEqual(manifest["products"][0]["name"], "data")
                self.assertEqual(payload["shape"], [2, 3, 4])
                self.assertEqual(len(payload["values"]), 3)
                self.assertEqual(volume_metadata["shape"], [2, 3, 4])
                self.assertEqual(volume.size, 24)
                self.assertTrue(np.all(volume == 1))
                with urllib.request.urlopen(base + "/") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "default-src 'self'",
                        response.headers["Content-Security-Policy"],
                    )

                with urllib.request.urlopen(
                    base + f"/?viewer={product_id}"
                ) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(
                        "frame-src 'self'", response.headers["Content-Security-Policy"]
                    )
                    self.assertIn(
                        "frame-ancestors 'self'",
                        response.headers["Content-Security-Policy"],
                    )

                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.putrequest(
                    "GET", "/../session.py", skip_accept_encoding=True
                )
                connection.putheader("Host", f"127.0.0.1:{server.server_port}")
                connection.endheaders()
                traversal = connection.getresponse()
                self.assertEqual(traversal.status, 404)
                traversal.read()
                connection.close()

                with self.assertRaises(urllib.error.HTTPError) as context:
                    urllib.request.urlopen(base + "/%2e%2e/session.py")
                self.assertEqual(context.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()

    def test_manifest_cannot_reference_files_outside_arrays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / "run"
            with spviz.Session(run_path) as run:
                run.capture("data", np.ones(4), axes=["sample"])
            manifest_path = run_path / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["products"][0]["file"] = "../outside.npy"
            np.save(root / "outside.npy", np.ones(4))
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "outside the run arrays"):
                RunStore(run_path)

    def test_manifest_rejects_path_like_ids_and_symlinked_array_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / "run"
            with spviz.Session(run_path) as run:
                run.capture("data", np.ones(4), axes=["sample"])
            manifest_path = run_path / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["products"][0]["id"] = "../../../escaped"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Invalid or duplicate product id"):
                RunStore(run_path)

            safe_run = root / "symlink-run"
            safe_run.mkdir()
            (safe_run / "manifest.json").write_text(json.dumps({}))
            (safe_run / "arrays").symlink_to(
                run_path / "arrays", target_is_directory=True
            )
            with self.assertRaisesRegex(ValueError, "must be a real directory"):
                RunStore(safe_run)

    def test_manifest_product_ids_have_a_bounded_safe_format(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                run.capture("data", np.ones(4), axes=["sample"])
            manifest_path = run_path / "manifest.json"
            manifest = json.loads(manifest_path.read_text())

            maximum_id = "a" * 128
            manifest["products"][0]["id"] = maximum_id
            manifest_path.write_text(json.dumps(manifest))
            self.assertIn(maximum_id, RunStore(run_path).products)

            manifest["products"][0]["id"] = "a" * 129
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Invalid or duplicate product id"):
                RunStore(run_path)

    def test_manifest_symlinks_are_rejected_at_open_and_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / "run"
            with spviz.Session(run_path) as run:
                run.capture("data", np.ones(4), axes=["sample"])
            store = RunStore(run_path)
            manifest_path = run_path / "manifest.json"
            external_manifest = root / "external-manifest.json"
            manifest_path.replace(external_manifest)
            manifest_path.symlink_to(external_manifest)

            with self.assertRaisesRegex(
                ValueError, "manifest cannot be a symbolic link"
            ):
                store.refresh()
            with self.assertRaisesRegex(
                ValueError, "manifest cannot be a symbolic link"
            ):
                RunStore(run_path)

    def test_arrays_directory_cannot_be_replaced_with_an_external_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / "run"
            with spviz.Session(run_path) as run:
                product_id = run.capture("data", np.ones(4), axes=["sample"])
            store = RunStore(run_path)
            arrays_path = run_path / "arrays"
            external_arrays = root / "external-arrays"
            arrays_path.replace(external_arrays)
            arrays_path.symlink_to(external_arrays, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "must be a real directory"):
                store.array(product_id)

    def test_deep_valid_lineage_is_validated_iteratively(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            arrays = run_path / "arrays"
            arrays.mkdir(parents=True)
            products = []
            for index in range(1100):
                filename = f"p{index}.npy"
                np.save(arrays / filename, np.array([index], dtype=np.int16))
                products.append(
                    {
                        "id": f"p{index}",
                        "name": f"product {index}",
                        "file": f"arrays/{filename}",
                        "shape": [1],
                        "dtype": "int16",
                        "bytes": 2,
                        "axes": ["sample"],
                        "view_axes": ["sample"],
                        "coordinates": {},
                        "upstream": [] if index == 0 else [f"p{index - 1}"],
                    }
                )
            manifest = {
                "format": "spviz-run",
                "version": 1,
                "name": "deep chain",
                "metadata": {},
                "products": list(reversed(products)),
            }
            (run_path / "manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(len(RunStore(run_path).products), 1100)


if __name__ == "__main__":
    unittest.main()
