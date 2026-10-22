#!/usr/bin/env python3
"""Manager release version; the engine retains its own mix.exs version."""
from pathlib import Path
import re
import sys


def read(root):
    value = (Path(root) / "scripts/release/manager-version").read_text()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?\n", value):
        raise ValueError("invalid manager release version")
    return value.rstrip("\n")


if __name__ == "__main__":
    try:
        print(read(Path(__file__).resolve().parents[2]))
    except (ValueError, OSError) as error:
        sys.exit(str(error))
