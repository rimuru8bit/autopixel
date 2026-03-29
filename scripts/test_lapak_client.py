#!/usr/bin/env python3
"""Quick smoke test for Lapakgaming Phase-1 client.

Usage:
  python scripts/test_lapak_client.py --all
  python scripts/test_lapak_client.py --categories
  python scripts/test_lapak_client.py --products [--category-code XXX]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.lapakgaming import LapakgamingClient
from services.lapakgaming.errors import LapakgamingError


def dump(title: str, payload: object) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    p = argparse.ArgumentParser(description="Lapakgaming client smoke test")
    p.add_argument("--categories", action="store_true")
    p.add_argument("--products", action="store_true")
    p.add_argument("--all", action="store_true", help="run all product endpoint")
    p.add_argument("--category-code", type=str, default=None)
    args = p.parse_args()

    if not (args.categories or args.products or args.all):
        args.categories = args.products = args.all = True

    try:
        client = LapakgamingClient()

        if args.categories:
            res = client.get_categories()
            dump("categories", res.raw)

        if args.products:
            res = client.get_products(category_code=args.category_code)
            dump("products", res.raw)

        if args.all:
            res = client.get_all_products()
            dump("all_products", res.raw)

        return 0
    except LapakgamingError as exc:
        print(f"Lapakgaming error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
