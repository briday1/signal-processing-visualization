import json
import tempfile
import threading
import unittest
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

    def test_http_api(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run"
            with spviz.Session(run_path) as run:
                product_id = run.capture("data", np.ones((2, 3, 4)), axes=["a", "b", "c"])
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(RunStore(run_path)))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(base + "/api/run") as response:
                    manifest = json.load(response)
                with urllib.request.urlopen(base + f"/api/product/{product_id}/slice?perm=0,1,2&layer=1") as response:
                    payload = json.load(response)
                with urllib.request.urlopen(base + f"/api/product/{product_id}/volume?perm=0,1,2&limit=4") as response:
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
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
