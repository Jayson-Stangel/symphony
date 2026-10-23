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


def validate_tag(root,tag):
    """Early source/base check; complete core hash checked after selection."""
    import subprocess
    base=read(root)
    if schema(root)==1:
        revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
        if re.fullmatch('v'+re.escape(base)+'-manager\.'+revision+'\.manifest\.[a-f0-9]{64}',tag) is None:
            raise ValueError('release tag must bind actual manager source and selected core hash')
    elif tag!='v'+base:raise ValueError('release tag differs from manager version')
    return tag


if __name__ == "__main__":
    try:
        if sys.argv[1:2]==['--validate-tag'] and len(sys.argv)==3:
            print(validate_tag(Path(__file__).resolve().parents[2],sys.argv[2]));sys.exit(0)
        if sys.argv[1:] not in ([],["--schema"]):raise ValueError("unknown manager release identity option")
        print(schema(Path(__file__).resolve().parents[2]) if sys.argv[1:] == ["--schema"] else read(Path(__file__).resolve().parents[2]))
    except (ValueError, OSError) as error:
        sys.exit(str(error))
