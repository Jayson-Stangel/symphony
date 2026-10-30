#!/usr/bin/env python3
"""Nonpublishing native cache qualification; no credentials or host mounts."""
import hashlib
from datetime import datetime
import re
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def build_timings(raw):
    """Retain per-vertex intervals; concurrent durations are never summed."""
    vertices = {}
    for line in raw.splitlines():
        event = json.loads(line)
        for vertex in event.get('vertexes', []):
            vertices[vertex['digest']] = dict(vertices.get(vertex['digest'], {}), **vertex)
    def instant(value):
        return datetime.fromisoformat(re.sub(r'(\.\d{6})\d+(?=Z|[+-])', r'\1', value).replace('Z', '+00:00'))
    intervals = []
    for key, vertex in vertices.items():
        if 'started' not in vertex or 'completed' not in vertex:
            continue
        name = vertex['name']
        category = ('cache_import' if name.startswith('importing cache manifest') else
                    'cache_export' if name.startswith(('exporting cache', 'exporting to GitHub Actions Cache')) else
                    'image_export_load' if name.startswith(('exporting to ', 'importing to docker')) else 'build')
        start, end = instant(vertex['started']), instant(vertex['completed'])
        if end < start:
            raise ValueError('negative BuildKit vertex interval')
        intervals.append({'vertex_id': key, 'category': category, 'started': vertex['started'],
                          'completed': vertex['completed'], 'seconds': (end-start).total_seconds(),
                          'cached': vertex.get('cached') is True})
    if not intervals:
        raise ValueError('missing completed BuildKit timing intervals')
    return {'intervals': intervals, 'cached_vertices': sum(i['cached'] for i in intervals),
            'note': 'Intervals overlap; do not sum them as wall-clock cost or billing.'}


def main():
    if sys.argv[1] == 'build-timing':
        phase, filename = sys.argv[2:]
        if phase not in ('cold', 'warm', 'changed'):
            raise ValueError('invalid build timing phase')
        source = Path(filename)
        source.with_name(phase+'-build-timing.json').write_text(json.dumps(build_timings(source.read_text()), indent=2)+'\n')
        return
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
    measurement = root/(phase+'-build.json')
    if measurement.exists():
        build = json.loads(measurement.read_text())
        result['builder_ref'] = build['builder_ref']
        result['cache_mode'] = build['cache_mode']
        for earlier in ('cold', 'warm'):
            previous = root/(earlier+'-build.json')
            if earlier != phase and previous.exists():
                old = json.loads(previous.read_text())
                if old['builder_ref'].split('/')[0] == build['builder_ref'].split('/')[0]:
                    raise ValueError('diagnostic phase reused a local builder; remote cache proof refused')
        result['fresh_builder_name'] = build['builder_ref'].split('/')[0]
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
            snapshot = root/(phase+'-inputs.json')
            input_flags = ['--inputs', str(snapshot)] if snapshot.exists() else []
            timed("record", ["python3", "scripts/release/record.py", "--image", digest,
                              "--provider", provider, "--architecture", architecture,
                              "--revision", revision, "--output", str(root / f"{phase}-record.json"), *input_flags])
            if snapshot.exists():
                result['input_identity'] = json.loads(snapshot.read_text())['input_identity']
            archive = root / f"{phase}.docker.tar.gz"
            started = time.monotonic()
            cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
            try:
                with archive.open("wb") as output:
                    save = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE, stderr=log)
                    try:
                        gzip = subprocess.Popen(["gzip", "-1"], stdin=save.stdout, stdout=output, stderr=log)
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
