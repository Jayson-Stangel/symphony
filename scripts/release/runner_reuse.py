#!/usr/bin/env python3
"""Select exact qualified runner components; never builds, relabels or deploys."""
import argparse
import json
from pathlib import Path
import subprocess

import artifacts
from component_provenance import verify_origin
from runner_inputs import identity


def plan(root, baseline, verify=verify_origin, inputs=identity):
    manager = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    choices = {}
    for provider in artifacts.PROVIDERS:
        choices[provider] = {}
        for architecture in artifacts.ARCHITECTURES:
            current = inputs(root, provider, architecture)
            decision = {"action": "build", "reason": "no qualified component with matching effective source inputs",
                        "input_identity": current["input_identity"], "inputs": current["inputs"]}
            original = baseline.get("runners", {}).get(provider, {}).get(architecture)
            if isinstance(original, dict) and original.get("input_identity") == current["input_identity"]:
                record = dict(original, provider=provider, architecture=architecture)
                try:
                    receipt = verify(record)
                    decision.update(action="reuse", reason="unchanged declared inputs; original qualified resolution retained",
                                    runner=original, verified_origin=receipt)
                except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
                    decision["reason"] = "original immutable native evidence/payload unavailable or incompatible; verified-input rebuild required"
            choices[provider][architecture] = decision
    return {"schema_version": 1, "manager_source_revision": manager, "components": choices}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--baseline", required=True, help="explicit source-bound baseline manifest, never a latest tag")
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-reuse", action="store_true")
    args = parser.parse_args()
    try:
        baseline = json.loads(Path(args.baseline).read_text())
        artifacts.validate(baseline, require_bundle=False)
        result = plan(args.root, baseline)
        Path(args.output).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        if args.require_reuse and any(item["action"] != "reuse" for group in result["components"].values() for item in group.values()):
            parser.exit(1, "runner reuse: at least one component requires a verified-input rebuild; plan retained\n")
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"runner reuse: {type(error).__name__}; no reuse decision was authorized\n")


if __name__ == "__main__":
    main()
