#!/usr/bin/env python3
"""Generate a sample workbook so the app can be demoed with no client data.

    python scripts/make_sample_workbook.py data/uniforms.xlsx
    python scripts/make_sample_workbook.py data/big.xlsx --employees 10000

The large variant exists to check that load times and paging hold up at the
client's real scale before anyone promises they will.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fixtures import clean_workbook, large_workbook, messy_workbook  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--employees", type=int, default=0,
                        help="generate a large workbook with this many employees")
    parser.add_argument("--messy", action="store_true",
                        help="generate the deliberately malformed variant")
    args = parser.parse_args()

    args.path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    if args.employees:
        large_workbook(args.path, employees=args.employees)
    elif args.messy:
        messy_workbook(args.path)
    else:
        clean_workbook(args.path)

    size = args.path.stat().st_size / 1e6
    print(f"wrote {args.path} ({size:.1f} MB) in {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
