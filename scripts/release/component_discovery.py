#!/usr/bin/env python3
"""Bounded discovery of immutable native records; selection is never tag based."""
import hashlib
import io
import json
import re
import zipfile

import artifacts
from component_provenance import api, MAX_RECORD_BYTES, require

MAX_PAGES = 3
MAX_RECORD_ARTIFACTS = 24


def discover(fetch=api, candidate_run=None):
    """Return untrusted candidates. Every selected component still needs verify_origin."""
    candidates = []
    inspected = 0
    prefix = f"actions/runs/{candidate_run}/artifacts" if candidate_run is not None else "actions/artifacts"
    for page in range(1, MAX_PAGES + 1):
        inventory = fetch(f"{prefix}?per_page=100&page={page}")
        batch = inventory.get("artifacts", [])
        require(isinstance(batch, list), "invalid artifact discovery response")
        for item in batch:
            if not re.fullmatch(r"runner-records-(codex|claude)-(arm64|x86_64)", item.get("name", "")):
                continue
            if item.get("expired") is not False or type(item.get("size_in_bytes")) is not int or not 0 < item["size_in_bytes"] <= MAX_RECORD_BYTES:
                continue
            if inspected >= MAX_RECORD_ARTIFACTS:
                return candidates
            inspected += 1
            try:
                data = fetch(f"actions/artifacts/{item['id']}/zip", binary=True)
                require(len(data) <= MAX_RECORD_BYTES and "sha256:" + hashlib.sha256(data).hexdigest() == item.get("digest"),
                        "discovered record artifact checksum differs")
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    require(sum(entry.file_size for entry in archive.infolist()) <= MAX_RECORD_BYTES,
                            "discovered record expands beyond limit")
                    for name in archive.namelist():
                        if not re.fullmatch(r"(?:previous/)?(?:codex|claude)-(?:arm64|x86_64)\.json", name):
                            continue
                        require(archive.namelist().count(name) == 1, "duplicate discovered native record")
                        record = json.loads(archive.read(name))
                        provider, arch = record.get("provider"), record.get("architecture")
                        require(provider in artifacts.PROVIDERS and arch in artifacts.ARCHITECTURES, "unknown discovered component")
                        require(item["name"] == f"runner-records-{provider}-{arch}", "discovered component artifact target differs")
                        proof = record.get("qualification", {})
                        require(proof.get("record_filename", f"{provider}-{arch}.json") == name,
                                "discovered record path differs from original proof")
                        if candidate_run is not None:
                            require(proof.get("record_origin", {}).get("run_id", proof.get("run_id")) == candidate_run, "foreign selected candidate run")
                        candidates.append(record)
            except (ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError):
                # Corrupt/old records cannot authorize reuse; keep searching within the bound.
                continue
        if len(batch) < 100:
            break
    return candidates
