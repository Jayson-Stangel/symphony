#!/usr/bin/env python3
"""Fail-closed identity for declared source/recipe inputs of one Docker target.

This supports the repository's explicit Dockerfile/COPY/ignore vocabulary. New
syntax must extend this reader with tests before an image using it is reusable.
It never substitutes repository HEAD for an unknown runtime input closure.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess

METADATA_ARGS = {"SYMPHONY_REVISION", "SYMPHONY_VERSION", "SYMPHONY_INPUT_IDENTITY"}
PLATFORMS = {"arm64": "linux/arm64", "x86_64": "linux/amd64"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def pattern_expression(pattern):
    # Closed subset of Docker filepath.Match + ** semantics. Negation, escaped
    # patterns and character classes need explicit handling before reuse.
    require(bool(pattern) and re.fullmatch(r"[A-Za-z0-9_.*/?-]+", pattern),
            "unsupported Docker ignore/COPY pattern; reuse refused")
    pattern = pattern.strip("/")
    result = ""
    index = 0
    while index < len(pattern):
        if pattern[index:index + 3] == "**/":
            result += "(?:.*/)?"
            index += 3
        elif pattern[index:index + 2] == "**":
            result += ".*"
            index += 2
        elif pattern[index] == "*":
            result += "[^/]*"
            index += 1
        elif pattern[index] == "?":
            result += "[^/]"
            index += 1
        else:
            result += re.escape(pattern[index])
            index += 1
    return re.compile(result + r"\Z")


def ignore_rules(contents):
    rules = []
    for line in contents.decode().splitlines():
        if not line or line.startswith("#"):
            continue
        require(line == line.strip() and line != ".", "ambiguous Docker ignore pattern; reuse refused")
        rules.append(pattern_expression(line))
    return rules


def ignored(path, rules):
    parts = PurePosixPath(path).parts
    return any(rule.fullmatch("/".join(parts[:n]))
               for rule in rules for n in range(1, len(parts) + 1))


def instructions(contents):
    pending = ""
    for line in contents.decode().splitlines():
        if not pending and (not line.strip() or line.lstrip().startswith("#")):
            require(not re.match(r"\s*#\s*(syntax|escape)\s*=", line),
                    "Docker parser directive needs explicit qualification; reuse refused")
            continue
        require("<<" not in line, "Docker heredoc needs explicit qualification; reuse refused")
        if line.rstrip().endswith("\\"):
            require(line.endswith("\\"), "ambiguous Docker continuation; reuse refused")
            pending += line[:-1] + " "
            continue
        value = (pending + line).strip()
        pending = ""
        match = re.fullmatch(r"([A-Za-z]+)\s+(.+)", value)
        require(match is not None, "unsupported Docker instruction; reuse refused")
        yield match[1].upper(), match[2]
    require(not pending, "incomplete Dockerfile; reuse refused")


def copy_sources(body):
    flags = {}
    while body.startswith("--"):
        match = re.match(r"--([a-z-]+)=([^\s]+)\s+", body)
        require(match is not None and match[1] in ("from", "chown", "chmod"),
                "unsupported COPY option; reuse refused")
        require(match[1] not in flags, "duplicate COPY option; reuse refused")
        flags[match[1]] = match[2]
        body = body[match.end():]
    paths = json.loads(body) if body.startswith("[") else shlex.split(body)
    require(isinstance(paths, list) and len(paths) >= 2 and all(isinstance(p, str) for p in paths),
            "unsupported COPY paths; reuse refused")
    return flags.get("from"), paths[:-1]


def stage_graph(contents):
    stages = {}
    current = None
    allowed = {"WORKDIR", "RUN", "ENV", "COPY", "ARG", "LABEL", "EXPOSE", "ENTRYPOINT", "USER", "CMD"}
    for operation, body in instructions(contents):
        if operation == "FROM":
            fields = shlex.split(body)
            require(len(fields) == 3 and fields[1].upper() == "AS" and fields[2] not in stages,
                    "FROM must name an explicit unique stage; reuse refused")
            base, _, name = fields
            require(base in stages or re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}", base),
                    "external base image is not pinned by digest; reuse refused")
            current = {"base": base, "instructions": [], "sources": [], "dependencies": []}
            if base in stages:
                current["dependencies"].append(base)
            stages[name] = current
            continue
        require(current is not None and operation in allowed, "unsupported production instruction; reuse refused")
        current["instructions"].append([operation, body])
        if operation == "RUN":
            require(not body.startswith("--"), "RUN flags can add hidden inputs; reuse refused")
        if operation == "COPY":
            source_stage, paths = copy_sources(body)
            if source_stage:
                require(source_stage in stages and stages[source_stage] is not current,
                        "external or recursive COPY stage; reuse refused")
                current["dependencies"].append(source_stage)
            else:
                current["sources"].extend(paths)
    return stages


def identity(root, provider, architecture, build_args=None):
    root = Path(root).resolve()
    require(provider in ("codex", "claude", "browser-tools") and architecture in PLATFORMS, "unsupported native runner target")
    build_args = dict(build_args or {})
    require(not (set(build_args) & METADATA_ARGS), "source/version metadata is recorded separately from runtime inputs")
    require(not (set(build_args) & {"TARGETARCH", "TARGETPLATFORM", "BUILDARCH", "BUILDPLATFORM"}),
            "explicit automatic platform override; reuse refused")
    dockerfile = root / "runner/Dockerfile"
    specific_ignore = root / "runner/Dockerfile.dockerignore"
    ignore = specific_ignore if specific_ignore.exists() else root / ".dockerignore"
    ignore_bytes = ignore.read_bytes() if ignore.exists() else b""
    rules = ignore_rules(ignore_bytes)
    stages = stage_graph(dockerfile.read_bytes())
    require(provider in stages, "production target is missing")
    selected = set()

    def visit(name):
        if name not in selected:
            selected.add(name)
            for dependency in stages[name]["dependencies"]:
                visit(dependency)
    visit(provider)
    recipe = {name: {"base": stage["base"], "instructions": stage["instructions"]}
              for name, stage in stages.items() if name in selected}
    metadata_scoped = set()
    for name, stage in stages.items():
        if name not in selected:
            continue
        scoped = stage["base"] in metadata_scoped
        for operation, body in stage["instructions"]:
            key = body.partition("=")[0] if operation == "ARG" else None
            if operation == "ARG" and key in METADATA_ARGS:
                scoped = True
            elif scoped:
                require(operation == "LABEL" or (operation == "ARG" and key == "TARGETARCH"),
                        "metadata build arguments can reach runtime instructions; reuse refused")
            if operation not in ("ARG", "LABEL"):
                require(not any(re.search(r"\$\{?" + re.escape(key) + r"(?:\}|\b)", body) for key in METADATA_ARGS),
                        "metadata variable affects runtime instructions; reuse refused")
        if scoped:
            metadata_scoped.add(name)
    declared = {}
    for name in selected:
        for operation, body in stages[name]["instructions"]:
            if operation == "ARG":
                key, separator, default = body.partition("=")
                require(re.fullmatch(r"[A-Z_][A-Z0-9_]*", key), "unsupported build argument")
                if key in METADATA_ARGS:
                    continue
                if key == "TARGETARCH":
                    declared[key] = PLATFORMS[architecture].split("/")[1]
                else:
                    require(separator or key in build_args, "missing effective build argument")
                    require(key not in declared or declared[key] == default, "ambiguous stage build argument")
                    declared[key] = build_args.get(key, default)
    require(set(build_args) <= set(declared), "unknown effective build argument; reuse refused")
    selectors = []
    for name in selected:
        for source in stages[name]["sources"]:
            require(not source.startswith("/") and ".." not in PurePosixPath(source).parts and "$" not in source,
                    "unsupported contextual COPY source; reuse refused")
            source = source.removeprefix("./").rstrip("/")
            selectors.append((source, pattern_expression(source) if source != "." else None))

    def consumed(path):
        if ignored(path, rules):
            return False
        parts = PurePosixPath(path).parts
        return any(source == "." or any(expression.fullmatch("/".join(parts[:n]))
                                       for n in range(1, len(parts) + 1))
                   for source, expression in selectors)

    raw = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-rz", "HEAD"])
    tracked = {}
    for entry in raw.split(b"\0"):
        if entry:
            header, path = entry.split(b"\t", 1)
            mode, kind, oid = header.decode().split()
            tracked[path.decode()] = (mode, kind, oid)
    def committed(path, data, info):
        require(path in tracked and tracked[path][1] == "blob", "uncommitted consumed file; reuse refused: " + path)
        mode, _, oid = tracked[path]
        require(mode in ("100644", "100755"), "unsupported consumed Git mode; reuse refused")
        actual_blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        require(actual_blob == oid and bool(info.st_mode & 0o111) == (mode == "100755"),
                "modified consumed file; reuse refused: " + path)
        return mode

    for metadata in (dockerfile, root / ".dockerignore", specific_ignore):
        relative_metadata = metadata.relative_to(root).as_posix()
        require(not metadata.is_symlink(), "nonregular build recipe/ignore file; reuse refused")
        require(relative_metadata not in tracked or metadata.exists(), "committed build recipe/ignore file is missing; reuse refused")
        if metadata.exists() and metadata in (dockerfile, ignore):
            info = metadata.lstat()
            require(stat.S_ISREG(info.st_mode), "nonregular build recipe/ignore file; reuse refused")
            committed(metadata.relative_to(root).as_posix(), metadata.read_bytes(), info)
    require(ignored(".git", rules) or not consumed(".git/config"),
            "Git metadata enters production context; reuse refused")
    files = {}
    directories = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        dirs[:] = [d for d in dirs if d != ".git" and not ignored((relative / d).as_posix(), rules)]
        for name in dirs:
            path = (relative / name).as_posix()
            if consumed(path) and not (Path(directory) / name).is_symlink():
                require(any(p.startswith(path + "/") and consumed(p) for p in tracked),
                        "uncommitted empty directory enters production context; reuse refused: " + path)
                directories[path] = stat.S_IMODE((Path(directory) / name).stat().st_mode)
        for name in [*names, *(d for d in dirs if (Path(directory) / d).is_symlink())]:
            path = (relative / name).as_posix()
            if not consumed(path):
                continue
            filename = root / path
            info = filename.lstat()
            require(stat.S_ISREG(info.st_mode), "consumed nonregular file; reuse refused: " + path)
            data = filename.read_bytes()
            mode = committed(path, data, info)
            files[path] = {"sha256": digest(data), "mode": mode, "permissions": stat.S_IMODE(info.st_mode)}
    expected = {path for path in tracked if consumed(path)}
    require(expected == set(files), "consumed Git inputs are missing; reuse refused")
    for source, expression in selectors:
        require(any(source == "." or any(expression.fullmatch("/".join(PurePosixPath(p).parts[:n]))
                                        for n in range(1, len(PurePosixPath(p).parts) + 1)) for p in files),
                "COPY source matched no committed inputs; reuse refused: " + source)
    value = {"schema_version": 1, "provider": provider, "platform": PLATFORMS[architecture],
             "recipe": recipe, "build_args": declared, "files": files, "directories": directories,
             "ignore_path": ignore.relative_to(root).as_posix(), "ignore_sha256": digest(ignore_bytes)}
    return {"input_identity": digest(canonical(value)), "inputs": value}


def project(root, target, architecture, effective_options):
    """Project actual production build options onto another selected stage closure."""
    stages = stage_graph((Path(root) / "runner/Dockerfile").read_bytes())
    require(target in stages, "projection target is missing")
    selected = set()
    def visit(name):
        if name not in selected:
            selected.add(name)
            for dependency in stages[name]["dependencies"]:
                visit(dependency)
    visit(target)
    keys = {body.partition("=")[0] for name in selected for operation, body in stages[name]["instructions"]
            if operation == "ARG"} - METADATA_ARGS - {"TARGETARCH"}
    return identity(root, target, architecture, {key: value for key, value in effective_options.items() if key in keys})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--provider", choices=("codex", "claude", "browser-tools"), required=True)
    parser.add_argument("--architecture", choices=tuple(PLATFORMS), required=True)
    parser.add_argument("--build-arg", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        options = dict(item.split("=", 1) for item in args.build_arg)
        require(len(options) == len(args.build_arg), "duplicate build arguments")
        result = identity(args.root, args.provider, args.architecture, options)
        Path(args.output).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"runner inputs: {error}\n")


if __name__ == "__main__":
    main()
