#!/usr/bin/env python3
"""Turn a password into the hash to put in AUTH_USERS.

    python scripts/hash_password.py 'their password'
    -> pbkdf2$240000$...$...

Then: AUTH_USERS=njiru:pbkdf2$240000$...$...:admin
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import hash_password  # noqa: E402


def main() -> int:
    password = sys.argv[1] if len(sys.argv) > 1 else getpass.getpass("Password: ")
    if not password:
        print("no password given", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
