#!/usr/bin/env python3
"""Read the authoritative engine application version without an Elixir toolchain."""
from pathlib import Path
import re
import sys

NUMBER = r"(?:0|[1-9][0-9]*)"
IDENTIFIER = r"(?:" + NUMBER + r"|[0-9]*[A-Za-z-][A-Za-z0-9-]*)"
VERSION = re.compile(NUMBER + r"\." + NUMBER + r"\." + NUMBER +
                     r"(?:-" + IDENTIFIER + r"(?:\." + IDENTIFIER + r")*)?")


def validate(value):
    if not isinstance(value, str) or VERSION.fullmatch(value) is None:
        raise ValueError("invalid engine version")
    return value


def read(root):
    source = (Path(root) / "elixir/mix.exs").read_text()
    declarations = re.findall(r'^\s*version:\s*"([^"\n]*)",\s*$', source, re.MULTILINE)
    if len(declarations) != 1 or len(re.findall(r"^\s*version:", source, re.MULTILINE)) != 1:
        raise ValueError("expected one literal engine version in elixir/mix.exs")
    return validate(declarations[0])


if __name__ == "__main__":
    try:
        if len(sys.argv) > 2:
            raise ValueError("usage: engine_version.py [SOURCE_ROOT]")
        print(read(sys.argv[1] if len(sys.argv) == 2 else Path(__file__).resolve().parents[2]))
    except (ValueError, OSError) as error:
        sys.exit(str(error))
