#!/usr/bin/env python3
"""Manager release version; the engine retains its own mix.exs version."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys


def read(root):
    value = (Path(root) / "scripts/release/manager-version").read_text()
    number = r"(?:0|[1-9][0-9]*)"
    identifier = r"(?:" + number + r"|[0-9]*[A-Za-z-][A-Za-z0-9-]*)"
    if not re.fullmatch(number + r"\." + number + r"\." + number +
                        r"(?:-" + identifier + r"(?:\." + identifier + r")*)?\n", value):
        raise ValueError("invalid manager release version")
    return value.rstrip("\n")


def schema(root):
    value=(Path(root)/"scripts/release/release-schema").read_text()
    if value not in ("1\n","2\n"):
        raise ValueError("invalid committed release schema policy")
    return int(value.rstrip("\n"))


def validate_tag(root,tag):
    """Early source/base check; complete core hash checked after selection."""
    base=read(root)
    if schema(root)==1:
        revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
        if re.fullmatch('v'+re.escape(base)+r'-manager\.'+revision+r'\.manifest\.[a-f0-9]{64}',tag) is None:
            raise ValueError('release tag must bind actual manager source and selected core hash')
    elif tag!='v'+base:raise ValueError('release tag differs from manager version')
    return tag


def validate_release_target(root, tag, run=subprocess.run):
    """Refuse completed releases before native fanout; publication checks again.

    This read-only GitHub lookup is only used by tag preflight. An unavailable
    service or malformed response cannot authorize expensive release work.
    """
    validate_tag(root, tag)
    if os.environ.get("GITHUB_REPOSITORY", "Jayson-Stangel/symphony") != "Jayson-Stangel/symphony":
        raise ValueError("release preflight supports only Jayson-Stangel/symphony; native fanout refused")
    try:
        response = run(["gh", "api", "--hostname", "github.com",
                        "repos/Jayson-Stangel/symphony/releases/tags/" + tag],
                       capture_output=True, text=True, timeout=30)
        value = json.loads(response.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError("release identity service unavailable; native fanout refused") from None
    if response.returncode:
        if isinstance(value, dict) and value.get("status") == "404":
            return tag
        raise ValueError("release identity service unavailable; native fanout refused")
    if not isinstance(value, dict) or value.get("tag_name") != tag or type(value.get("draft")) is not bool:
        raise ValueError("invalid release identity service response; native fanout refused")
    if not value["draft"]:
        raise ValueError("refusing to alter a completed release before native fanout")
    return tag


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ['--validate-release-target'] and len(sys.argv) == 3:
            print(validate_release_target(Path(__file__).resolve().parents[2], sys.argv[2]));sys.exit(0)
        if sys.argv[1:2]==['--validate-tag'] and len(sys.argv)==3:
            print(validate_tag(Path(__file__).resolve().parents[2],sys.argv[2]));sys.exit(0)
        if sys.argv[1:] not in ([],["--schema"]):raise ValueError("unknown manager release identity option")
        print(schema(Path(__file__).resolve().parents[2]) if sys.argv[1:] == ["--schema"] else read(Path(__file__).resolve().parents[2]))
    except (ValueError, OSError) as error:
        sys.exit(str(error))
