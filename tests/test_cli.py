import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from spviz.cli import main, parser


class CliTests(unittest.TestCase):
    def test_custom_long_port(self):
        args = parser().parse_args(["serve", "a-run", "--port", "9000"])
        self.assertEqual(args.port, 9000)

    def test_custom_short_port(self):
        args = parser().parse_args(["serve", "a-run", "-p", "9001"])
        self.assertEqual(args.port, 9001)

    def test_port_accepts_full_tcp_range(self):
        for value, expected in (("0", 0), ("65535", 65535)):
            with self.subTest(value=value):
                args = parser().parse_args(["serve", "a-run", "--port", value])
                self.assertEqual(args.port, expected)

    def test_port_rejects_invalid_values_with_a_friendly_error(self):
        for value in ("-1", "65536", "not-a-port"):
            with self.subTest(value=value):
                errors = io.StringIO()
                with redirect_stderr(errors), self.assertRaises(SystemExit) as context:
                    parser().parse_args(["serve", "a-run", "--port", value])
                self.assertEqual(context.exception.code, 2)
                self.assertIn("port must", errors.getvalue())

    def test_static_export_size_limits(self):
        args = parser().parse_args(
            [
                "export-static",
                "a-run",
                "a-site",
                "--max-volume-mb",
                "32",
                "--max-total-mb",
                "0",
            ]
        )
        self.assertEqual(args.max_volume_mb, 32)
        self.assertEqual(args.max_total_mb, 0)

    def test_static_export_rejects_non_finite_or_non_positive_size_limits(self):
        invalid_volume_limits = ("0", "-1", "nan", "inf", "0.000001", "1e308")
        for value in invalid_volume_limits:
            with self.subTest(option="max-volume", value=value):
                errors = io.StringIO()
                with redirect_stderr(errors), self.assertRaises(SystemExit) as context:
                    parser().parse_args(
                        ["export-static", "a-run", "a-site", "--max-volume-mb", value]
                    )
                self.assertEqual(context.exception.code, 2)
                self.assertIn("size must", errors.getvalue())

        invalid_total_limits = ("-1", "nan", "inf", "0.000001", "1e308")
        for value in invalid_total_limits:
            with self.subTest(option="max-total", value=value):
                errors = io.StringIO()
                with redirect_stderr(errors), self.assertRaises(SystemExit) as context:
                    parser().parse_args(
                        ["export-static", "a-run", "a-site", "--max-total-mb", value]
                    )
                self.assertEqual(context.exception.code, 2)
                self.assertIn("size must", errors.getvalue())

    def test_expected_serve_errors_are_reported_without_a_traceback(self):
        errors = io.StringIO()
        with (
            patch("spviz.cli.serve", side_effect=FileNotFoundError("missing manifest")),
            redirect_stderr(errors),
            self.assertRaises(SystemExit) as context,
        ):
            main(["serve", "missing-run"])
        self.assertEqual(context.exception.code, 2)
        self.assertIn("missing manifest", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
