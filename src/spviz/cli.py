from __future__ import annotations

import argparse

from .server import serve
from .static import export_static


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="spviz", description="Visualize signal-processing data products")
    sub = result.add_subparsers(dest="command", required=True)
    serve_parser = sub.add_parser("serve", help="Serve a captured run in the browser")
    serve_parser.add_argument("run_dir", help="Directory containing manifest.json")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("-p", "--port", default=8765, type=int, metavar="PORT", help="TCP port to listen on (default: 8765)")
    export_parser = sub.add_parser("export-static", help="Export a run for static hosting")
    export_parser.add_argument("run_dir", help="Directory containing manifest.json")
    export_parser.add_argument("output_dir", help="Directory to create")
    return result


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.command == "serve":
        serve(args.run_dir, args.host, args.port)
    elif args.command == "export-static":
        print(export_static(args.run_dir, args.output_dir))


if __name__ == "__main__":
    main()
