#!/usr/bin/env python3
"""Operator entrypoint: ingest, index, sync, coach, lint."""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="rbrain",
        description="rbrain toolchain: atomize, backlinks, vector index, coach, lint",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ingest", help="Run atomizer then backlinker")
    sub.add_parser("index", help="Rebuild vector_index.json")
    sub.add_parser("sync", help="ingest + index")
    sub.add_parser("coach", help="Start coach REPL")
    sub.add_parser("lint", help="Check atom wikilinks and trace sections")

    args = parser.parse_args()

    if args.command == "ingest":
        from atomizer import WikiAtomizer
        from backlinker import BackLinker

        WikiAtomizer().run()
        BackLinker().run()
        return 0

    if args.command == "index":
        from vector_index import WikiHybridIndexer

        WikiHybridIndexer().run_indexing()
        return 0

    if args.command == "sync":
        from atomizer import WikiAtomizer
        from backlinker import BackLinker
        from vector_index import WikiHybridIndexer

        WikiAtomizer().run()
        BackLinker().run()
        WikiHybridIndexer().run_indexing()
        return 0

    if args.command == "coach":
        from coach import main_repl

        main_repl()
        return 0

    if args.command == "lint":
        from rbrain_lint import main as lint_main

        return lint_main()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
