import unittest

from spviz.cli import parser


class CliTests(unittest.TestCase):
    def test_custom_long_port(self):
        args = parser().parse_args(["serve", "a-run", "--port", "9000"])
        self.assertEqual(args.port, 9000)

    def test_custom_short_port(self):
        args = parser().parse_args(["serve", "a-run", "-p", "9001"])
        self.assertEqual(args.port, 9001)


if __name__ == "__main__":
    unittest.main()
