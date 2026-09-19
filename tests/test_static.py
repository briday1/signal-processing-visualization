import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
            import hashlib
            index = (site / "index.html").read_text()
            for name in ("style.css", "range.css", "config.js", "gif.js", "app.js"):
                revision = hashlib.sha256((site / name).read_bytes()).hexdigest()[:16]
                self.assertIn(f'"./{name}?v={revision}"', index)

            self.assertIn('option value="viridis"', (site / "index.html").read_text())
            self.assertIn('option value="equal"', (site / "index.html").read_text())
            self.assertIn('id="overview-aspect"', (site / "index.html").read_text())
            self.assertIn('id="view-mode"', (site / "index.html").read_text())
            self.assertIn("SPVIZ_STATIC_BASE", (site / "config.js").read_text())
            self.assertIn("SPVIZ_GALLERY_URL", (site / "config.js").read_text())
            manifest = json.loads((site / "data" / "run.json").read_text())
            product_id = manifest["products"][0]["id"]
            volumes = list((site / "data" / "volumes").glob(f"{product_id}--*.f32"))
            self.assertEqual(len(volumes), 6)
            self.assertEqual(manifest["static_export"]["volume_count"], 6)
            self.assertEqual(manifest["static_export"]["capped_volumes"], 0)
            for volume in volumes:
                metadata = json.loads(volume.with_suffix(".json").read_text())
                self.assertEqual(
                    metadata["static_export"]["payload_bytes"], 2 * 3 * 4 * 4
                )
                self.assertFalse(metadata["static_export"]["plane_capped"])
                self.assertFalse(metadata["static_export"]["depth_capped"])

    def test_regeneration_replaces_the_site_and_removes_stale_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with spviz.Session(root / "run") as run:
                run.capture("trace", np.arange(8))
            site = export_static(root / "run", root / "site")
            (site / "stale.txt").write_text("from an older export")

            export_static(root / "run", site)

            self.assertFalse((site / "stale.txt").exists())
            self.assertTrue((site / ".spviz-static").is_file())

    def test_failed_regeneration_preserves_the_previous_complete_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with spviz.Session(root / "run") as run:
                run.capture("trace", np.arange(8))
            site = export_static(root / "run", root / "site")
            sentinel = site / "previous-export.txt"
            sentinel.write_text("keep me")

            with (
                patch(
                    "spviz.static.RunStore.volume_binary",
                    side_effect=RuntimeError("render failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "render failed"),
            ):
                export_static(root / "run", site)

            self.assertEqual(sentinel.read_text(), "keep me")
            self.assertEqual(list(root.glob(".site.staging-*")), [])

    def test_output_must_not_overlap_the_run_or_replace_unrelated_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_path = root / "run"
            with spviz.Session(run_path) as run:
                run.capture("trace", np.arange(8))

            with self.assertRaisesRegex(ValueError, "must not overlap"):
                export_static(run_path, run_path / "site")
            with self.assertRaisesRegex(ValueError, "must not overlap"):
                export_static(run_path, root)

            unrelated = root / "unrelated"
            unrelated.mkdir()
            personal_file = unrelated / "notes.txt"
            personal_file.write_text("important")
            with self.assertRaisesRegex(ValueError, "not an spviz static export"):
                export_static(run_path, unrelated)
            self.assertEqual(personal_file.read_text(), "important")

    def test_volume_budget_caps_plane_density_and_records_exact_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with spviz.Session(root / "run") as run:
                run.capture("large plane", np.ones((100, 120)), axes=["row", "column"])

            site = export_static(
                root / "run",
                root / "site",
                max_volume_bytes=4_000,
                max_total_volume_bytes=None,
            )

            metadata_files = list((site / "data" / "volumes").glob("*.json"))
            self.assertEqual(len(metadata_files), 2)
            for metadata_file in metadata_files:
                metadata = json.loads(metadata_file.read_text())
                quality = metadata["static_export"]
                self.assertLessEqual(quality["payload_bytes"], 4_000)
                self.assertEqual(
                    quality["exported_plane_shape"],
                    [metadata["rows"], metadata["columns"]],
                )
                self.assertTrue(quality["plane_capped"])
                self.assertFalse(quality["depth_capped"])
                self.assertEqual(
                    metadata_file.with_suffix(".f32").stat().st_size,
                    quality["payload_bytes"],
                )

    def test_too_little_budget_fails_without_publishing_a_partial_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with spviz.Session(root / "run") as run:
                run.capture(
                    "deep", np.ones((100, 2, 2)), axes=["depth", "row", "column"]
                )

            with self.assertRaisesRegex(ValueError, "preserving its 100 layers"):
                export_static(
                    root / "run",
                    root / "site",
                    max_volume_bytes=200,
                    max_total_volume_bytes=None,
                )
            self.assertFalse((root / "site").exists())
            self.assertEqual(list(root.glob(".site.staging-*")), [])

    def test_total_budget_is_shared_across_every_axis_permutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with spviz.Session(root / "run") as run:
                run.capture(
                    "cube", np.ones((5, 20, 20)), axes=["depth", "row", "column"]
                )

            site = export_static(
                root / "run",
                root / "site",
                max_volume_bytes=4_000,
                max_total_volume_bytes=2_400,
            )

            volumes = list((site / "data" / "volumes").glob("*.f32"))
            manifest = json.loads((site / "data" / "run.json").read_text())
            self.assertEqual(len(volumes), 6)
            self.assertLessEqual(
                sum(volume.stat().st_size for volume in volumes), 2_400
            )
            self.assertEqual(manifest["static_export"]["max_bytes_per_volume"], 400)
            self.assertEqual(manifest["static_export"]["max_total_volume_bytes"], 2_400)

    def test_budget_arguments_are_validated_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with spviz.Session(root / "run") as run:
                run.capture("trace", np.arange(8))
            with self.assertRaisesRegex(ValueError, "max_volume_bytes"):
                export_static(root / "run", root / "site", max_volume_bytes=3)
            with self.assertRaisesRegex(ValueError, "max_total_volume_bytes"):
                export_static(root / "run", root / "site", max_total_volume_bytes=True)
            self.assertFalse((root / "site").exists())

    def test_custom_budget_can_exceed_dynamic_server_safety_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with spviz.Session(root / "run") as run:
                run.capture(
                    "wide",
                    np.ones((2001, 2001), dtype=np.uint8),
                    axes=["row", "column"],
                )
            site = export_static(
                root / "run",
                root / "site",
                max_volume_bytes=17_000_000,
                max_total_volume_bytes=None,
            )
            metadata = json.loads(
                next((site / "data" / "volumes").glob("*.json")).read_text()
            )
            self.assertEqual(metadata["rows"], 2001)
            self.assertEqual(metadata["columns"], 2001)
            self.assertFalse(metadata["static_export"]["plane_capped"])


if __name__ == "__main__":
    unittest.main()
