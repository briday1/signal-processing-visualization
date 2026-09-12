import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from examples import gallery


class GalleryScienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        root = Path(cls.temporary.name)
        generators = {
            "radar": gallery.generate_radar,
            "audio": gallery.generate_audio,
            "comms": gallery.generate_comms,
            "seismic": gallery.generate_seismic,
            "ecg": gallery.generate_ecg,
            "pulse": gallery.generate_pulse_compression,
            "lowpass": gallery.generate_equalizer,
            "bearing": gallery.generate_bearing,
            "localization": gallery.generate_localization,
            "ofdm": gallery.generate_ofdm,
        }
        cls.runs = {}
        cls.manifests = {}
        for name, generator in generators.items():
            run = generator(root / name)
            cls.runs[name] = run
            cls.manifests[name] = json.loads((run / "manifest.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def products(self, run_name):
        return {
            product["name"]: product for product in self.manifests[run_name]["products"]
        }

    def array(self, run_name, product):
        if isinstance(product, str):
            product = self.products(run_name)[product]
        return np.load(self.runs[run_name] / product["file"])

    def test_products_have_coordinates_valid_lineage_and_binary_semantics(self):
        for run_name, manifest in self.manifests.items():
            with self.subTest(run=run_name):
                product_ids = {product["id"] for product in manifest["products"]}
                for product in manifest["products"]:
                    self.assertEqual(set(product["coordinates"]), set(product["axes"]))
                    self.assertTrue(set(product["upstream"]).issubset(product_ids))
                    values = self.array(run_name, product)
                    if not (
                        run_name == "radar"
                        and product["name"] == "Cell-average noise estimate"
                    ):
                        self.assertTrue(np.all(np.isfinite(values)))
                    if product["units"] == "binary":
                        self.assertTrue(set(np.unique(values)).issubset({0, 1}))

    def test_continuous_causal_sta_lta_separates_event_from_noise(self):
        metadata = self.manifests["seismic"]["metadata"]
        trigger = self.array("seismic", "Event trigger mask")
        event_fraction = float(trigger[:, 9:16].mean())
        noise_fraction = float(
            np.concatenate([trigger[:, :9], trigger[:, 16:]], axis=1).mean()
        )
        self.assertGreater(event_fraction, 0.04)
        self.assertLess(noise_fraction, 0.005)
        self.assertGreater(event_fraction, 20 * noise_fraction)
        self.assertAlmostEqual(metadata["event_trigger_fraction"], event_fraction)
        self.assertAlmostEqual(metadata["noise_trigger_fraction"], noise_fraction)

    def test_pulse_compression_peaks_and_coordinates_match_echo_delay(self):
        metadata = self.manifests["pulse"]["metadata"]
        products = self.products("pulse")
        compressed = self.array("pulse", "Compressed range profile")
        detections = self.array("pulse", "Range detections")
        coordinate_file = products["Compressed range profile"]["coordinates"][
            "range bin"
        ]["file"]
        ranges = np.load(self.runs["pulse"] / coordinate_file)
        expected_bins = []
        for target in metadata["targets"]:
            delay = target["delay_bin"]
            expected_bins.append(delay)
            local = compressed[delay - 2 : delay + 3]
            self.assertEqual(delay - 2 + int(np.argmax(local)), delay)
            self.assertEqual(int(detections[delay]), 1)
            self.assertAlmostEqual(float(ranges[delay]), target["range_m"])
        self.assertEqual(np.flatnonzero(detections).tolist(), expected_bins)

    def test_qpsk_symbol_products_have_truthful_lineage(self):
        products = self.products("comms")
        sampled = products["Sampled symbol phase grid"]
        self.assertEqual(sampled["representation"], "phase")
        self.assertEqual(sampled["shape"], [20, 32])
        self.assertEqual(sampled["upstream"], [products["Matched-filter frames"]["id"]])
        self.assertEqual(products["Constellation density"]["upstream"], [sampled["id"]])
        self.assertEqual(
            products["Decision-error density"]["upstream"], [sampled["id"]]
        )
        errors = self.array("comms", "Decision-error density")
        self.assertEqual(
            int(errors.sum()),
            self.manifests["comms"]["metadata"]["total_symbol_errors"],
        )

    def test_ofdm_equalization_is_tapped_before_quality_and_decisions(self):
        products = self.products("ofdm")
        equalized = products["Equalized resource-grid cube"]
        self.assertEqual(
            set(equalized["upstream"]),
            {
                products["Received resource-grid cube"]["id"],
                products["Pilot channel estimate"]["id"],
            },
        )
        self.assertEqual(products["Mean EVM map"]["upstream"], [equalized["id"]])
        self.assertEqual(products["Decision-error map"]["upstream"], [equalized["id"]])
        error_mask = self.array("ofdm", "Decision-error map")
        self.assertFalse(np.any(error_mask[0]))
        self.assertGreater(int(error_mask.sum()), 0)
        self.assertEqual(
            int(error_mask.sum()),
            self.manifests["ofdm"]["metadata"]["error_cells_after_frame_collapse"],
        )

    def test_radar_cfar_edges_are_undefined_and_targets_are_detected(self):
        products = self.products("radar")
        noise = self.array("radar", "Cell-average noise estimate")
        detections = self.array("radar", "CFAR detection mask")
        valid = np.zeros(noise.shape, dtype=bool)
        valid[:, 3:-3, 5:-5] = True
        self.assertTrue(np.all(np.isfinite(noise[valid])))
        self.assertTrue(np.all(np.isnan(noise[~valid])))
        self.assertFalse(np.any(detections[~valid]))
        angle_file = products["CFAR detection mask"]["coordinates"]["look angle"][
            "file"
        ]
        angles = np.load(self.runs["radar"] / angle_file)
        for target in self.manifests["radar"]["metadata"]["targets"]:
            angle_index = int(np.argmin(np.abs(angles - target["angle_deg"])))
            doppler_index = round(target["doppler_bin"]) + noise.shape[1] // 2
            range_index = round(target["range_bin"])
            neighborhood = detections[
                angle_index - 2 : angle_index + 3,
                doppler_index - 2 : doppler_index + 3,
                range_index - 2 : range_index + 3,
            ]
            self.assertGreater(int(neighborhood.sum()), 0)

    def test_low_pass_demo_suppresses_out_of_band_tone(self):
        self.assertEqual(self.manifests["lowpass"]["name"], "Audio FIR low-pass filter")
        products = self.products("lowpass")
        before = self.array("lowpass", "Input power spectrum")
        after = self.array("lowpass", "Low-pass output spectrum")
        coordinate_file = products["Input power spectrum"]["coordinates"]["frequency"][
            "file"
        ]
        frequency = np.load(self.runs["lowpass"] / coordinate_file)
        passband = int(np.argmin(np.abs(frequency - 440)))
        stopband = int(np.argmin(np.abs(frequency - 3_100)))
        self.assertGreater(float(after[passband] / before[passband]), 0.95)
        self.assertLess(float(after[stopband] / before[stopband]), 1e-5)

    def test_localization_reports_angle_and_physical_frame_time(self):
        metadata = self.manifests["localization"]["metadata"]
        self.assertLessEqual(abs(metadata["angle_error_deg"]), 5.0)
        products = self.products("localization")
        descriptor = products["Beam time-frequency cube"]["coordinates"]["frame"]
        frame_times = np.load(self.runs["localization"] / descriptor["file"])
        self.assertEqual(descriptor["units"], "ms")
        self.assertAlmostEqual(float(frame_times[0]), 63.5 / 8_000 * 1e3)
        self.assertTrue(np.all(np.diff(frame_times) > 0))

    def test_audio_uses_candidate_not_tracking_terminology(self):
        products = self.products("audio")
        self.assertIn("Tone candidate mask", products)
        self.assertNotIn("Tracked tone mask", products)
        spectrum = self.array("audio", "Short-time spectrum")
        descriptor = products["Short-time spectrum"]["coordinates"]["look angle"]
        angles = np.load(self.runs["audio"] / descriptor["file"])
        estimated_angle = float(angles[int(np.argmax(spectrum.sum(axis=(1, 2))))])
        true_angle = self.manifests["audio"]["metadata"]["source_angle_deg"]
        self.assertLessEqual(abs(estimated_angle - true_angle), 10.0)

    def test_ecg_has_exactly_one_peak_per_lead_and_beat(self):
        detections = self.array("ecg", "R-peak detections")
        np.testing.assert_array_equal(detections.sum(axis=2), 1)
        peak_samples = np.argmax(detections, axis=2)
        self.assertLessEqual(int(np.max(np.abs(peak_samples - 62))), 2)


if __name__ == "__main__":
    unittest.main()
