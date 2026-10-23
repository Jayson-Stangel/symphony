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


def schema(root):
    value=(Path(root)/"scripts/release/release-schema").read_text()
    if value not in ("1\n","2\n"):
        raise ValueError("invalid committed release schema policy")
    return int(value.rstrip("\n"))


if __name__ == "__main__":
    try:
        if sys.argv[1:] not in ([],["--schema"]):raise ValueError("unknown manager release identity option")
        print(schema(Path(__file__).resolve().parents[2]) if sys.argv[1:] == ["--schema"] else read(Path(__file__).resolve().parents[2]))
    except (ValueError, OSError) as error:
        sys.exit(str(error))
