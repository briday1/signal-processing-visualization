from __future__ import annotations

import argparse

from .server import serve


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="spviz", description="Visualize signal-processing data products")
    sub = result.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve", help="Serve a captured run in the browser")
    serve_parser.add_argument("run_dir", help="Directory containing manifest.json")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("-p", "--port", default=8765, type=int, metavar="PORT", help="TCP port to listen on (default: 8765)")
    return result


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.command == "serve":
        serve(args.run_dir, args.host, args.port)


if __name__ == "__main__":
    main()
