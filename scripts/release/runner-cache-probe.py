#!/usr/bin/env python3
"""Nonpublishing native cache qualification; no credentials or host mounts."""
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def main():
    phase = sys.argv[1]
    if phase not in ("cold", "warm", "changed"):
        raise ValueError("invalid diagnostic phase")
    provider = os.environ["PROVIDER"]
    architecture = os.environ["ARCHITECTURE"]
    if provider not in ("codex", "claude") or architecture not in ("arm64", "x86_64"):
        raise ValueError("invalid native target")
    root = Path(os.environ["RUNNER_TEMP"]) / "runner-cache-measurements"
    root.mkdir(exist_ok=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    image = f"localhost:5000/symphony-{provider}:cache-probe"
    result = {"phase": phase, "provider": provider, "architecture": architecture,
              "source_revision": revision, "passed": False}
    with (root / f"{phase}.log").open("w") as log:
        def timed(name, command):
            started = time.monotonic()
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
            result[name + "_seconds"] = time.monotonic() - started

        try:
            timed("push", ["docker", "push", image])
            digest = subprocess.check_output(["docker", "image", "inspect", "--format",
                                              "{{index .RepoDigests 0}}", image], text=True).strip()
            result["digest"] = digest
            # Pull may use local layers. The smoke still executes the actual digest;
            # this probe does not claim an uncached registry/network pull.
            timed("pull", ["docker", "pull", digest])
            timed("qualification", ["bash", "scripts/release/runner-smoke.sh", digest,
                                     provider, architecture, revision])
            timed("record", ["python3", "scripts/release/record.py", "--image", digest,
                              "--provider", provider, "--architecture", architecture,
                              "--revision", revision, "--output", str(root / f"{phase}-record.json")])
            archive = root / f"{phase}.docker.tar.gz"
            started = time.monotonic()
            cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
            try:
                with archive.open("wb") as output:
                    save = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE, stderr=log)
                    try:
                        gzip = subprocess.Popen(["gzip"], stdin=save.stdout, stdout=output, stderr=log)
                    except BaseException:
                        save.stdout.close()
                        save.terminate()
                        save.wait(timeout=30)
                        raise
                    save.stdout.close()
                    compressed = gzip.wait()
                    saved = save.wait()
                if compressed or saved:
                    raise RuntimeError(f"archive pipeline failed: save={saved}, gzip={compressed}")
                result["export_compress_seconds"] = time.monotonic() - started
                cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
                result["export_compress_cpu_seconds"] = (
                    cpu_after.ru_utime + cpu_after.ru_stime - cpu_before.ru_utime - cpu_before.ru_stime)
                result["archive_bytes"] = archive.stat().st_size
                started = time.monotonic()
                checksum = hashlib.sha256()
                with archive.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        checksum.update(block)
                result["archive_sha256"] = checksum.hexdigest()
                result["checksum_seconds"] = time.monotonic() - started
            finally:
                archive.unlink(missing_ok=True)
            result["passed"] = True
        finally:
            (root / f"{phase}-qualification.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
