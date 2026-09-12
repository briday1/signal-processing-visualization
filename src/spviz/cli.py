from __future__ import annotations

import argparse
import math

from .server import serve
from .static import export_static


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "port must be an integer from 0 through 65535"
        ) from error
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be from 0 through 65535")
    return port


def _megabytes(value: str, *, allow_zero: bool = False) -> float:
    try:
        amount = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "size must be a finite number of megabytes"
        ) from error
    if not math.isfinite(amount):
        raise argparse.ArgumentTypeError("size must be a finite number of megabytes")
    if not math.isfinite(amount * 1_000_000):
        raise argparse.ArgumentTypeError(
            "size must be small enough to convert to bytes"
        )
    if allow_zero and amount == 0:
        return amount
    if amount < 4 / 1_000_000:
        qualifier = "zero (unlimited) or at least" if allow_zero else "at least"
        raise argparse.ArgumentTypeError(f"size must be {qualifier} 0.000004 MB")
    return amount


def _positive_megabytes(value: str) -> float:
    return _megabytes(value)


def _total_megabytes(value: str) -> float:
    return _megabytes(value, allow_zero=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="spviz", description="Visualize signal-processing data products"
    )
    sub = result.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve", help="Serve a captured run in the browser")
    serve_parser.add_argument("run_dir", help="Directory containing manifest.json")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument(
        "-p",
        "--port",
        default=8765,
        type=_port,
        metavar="PORT",
        help="TCP port to listen on (default: 8765)",
    )
    export_parser = sub.add_parser(
        "export-static", help="Export a run for static hosting"
    )
    export_parser.add_argument("run_dir", help="Directory containing manifest.json")
    export_parser.add_argument("output_dir", help="Directory to create")
    export_parser.add_argument(
        "--max-volume-mb",
        default=16.0,
        type=_positive_megabytes,
        metavar="MB",
        help="Maximum payload for one exported axis permutation (default: 16)",
    )
    export_parser.add_argument(
        "--max-total-mb",
        default=256.0,
        type=_total_megabytes,
        metavar="MB",
        help="Maximum total volume payload; use 0 for unlimited (default: 256)",
    )
    return result


def main(argv: list[str] | None = None) -> None:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    try:
        if args.command == "serve":
            serve(args.run_dir, args.host, args.port)
        elif args.command == "export-static":
            maximum_total = (
                None if args.max_total_mb == 0 else round(args.max_total_mb * 1_000_000)
            )
            print(
                export_static(
                    args.run_dir,
                    args.output_dir,
                    max_volume_bytes=round(args.max_volume_mb * 1_000_000),
                    max_total_volume_bytes=maximum_total,
                )
            )
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        command_parser.error(str(error))


if __name__ == "__main__":
    main()
