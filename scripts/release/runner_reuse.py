#!/usr/bin/env python3
"""Plan exact qualified runner components; never builds, relabels or deploys."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import re

import artifacts
from component_discovery import discover
from component_provenance import verify_origin
from runner_inputs import identity


def input_delta(previous, current):
    """Compare complete Docker-derived snapshots, including removed COPY inputs."""
    old, new = previous['inputs'], current['inputs']
    paths = sorted(path for path in set(old.get('files', {})) | set(new.get('files', {}))
                   if old.get('files', {}).get(path) != new.get('files', {}).get(path))
    controls = [key for key in ('recipe', 'build_args', 'directories', 'ignore_path', 'ignore_sha256', 'platform')
                if old.get(key) != new.get(key)]
    return {'changed_effective_paths': paths, 'changed_input_controls': controls}


def previous_inputs(root, record):
    """Local read-only source reconstruction for explanation, never an admission path.

    Missing history or old unsupported recipes remain an explicit diagnostic gap;
    they never turn an input mismatch into reuse. No ref fetch or source code runs.
    """
    revision = record.get('source_revision', '')
    artifacts.require(re.fullmatch(r'[a-f0-9]{40}', revision) is not None, 'invalid diagnostic source')
    dependencies = record.get('dependencies', {})
    artifacts.require(isinstance(dependencies, dict), 'invalid diagnostic dependencies')
    options = dependencies.get('build_options', {})
    artifacts.require(isinstance(options, dict) and
                      all(isinstance(k, str) and isinstance(v, str) for k, v in options.items()),
                      'invalid diagnostic build options')
    # Source snapshots are data, not a checkout that may invoke developer hooks
    # or source-selected global smudge filters. Do not inherit Git overrides.
    environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    subprocess.run(['git', '-C', str(root), 'cat-file', '-e', revision + '^{commit}'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
    with tempfile.TemporaryDirectory(prefix='symphony-input-diagnostic-') as folder:
        subprocess.run(['git', '-c', 'core.hooksPath='+os.devnull, 'clone', '--quiet', '--shared', '--no-checkout', str(root), folder],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
        subprocess.run(['git', '-c', 'core.hooksPath='+os.devnull, '-C', folder, 'checkout', '--quiet', '--no-recurse-submodules', '--detach', revision],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment)
        options = {k: v for k, v in options.items()
                   if k != 'TARGETARCH'}
        return identity(folder, record['provider'], record['architecture'], options)


def evidence_refusal(error):
    # Never include arbitrary service stderr, token-bearing URLs or candidate
    # strings in plans. Fixed categories expose the failing boundary safely.
    text = str(error).lower() if isinstance(error, ValueError) else ''
    for category, words in (
        ('expired_or_missing_payload', ('expired', 'absent')),
        ('untrusted_source_or_workflow', ('history', 'repository', 'controller', 'workflow', 'ancestry')),
        ('wrong_native_platform', ('native platform',)),
        ('failed_or_missing_qualification', ('did not pass', 'missing or ambiguous', 'cancelled')),
        ('origin_service_unavailable', ('service unavailable',)),
        ('immutable_integrity_mismatch', ('checksum', 'digest', 'corrupt', 'record differs', 'origin'))):
        if any(word in text for word in words):
            return category
    return 'incompatible_or_unavailable_evidence'


def plan(root, baseline, verify=verify_origin, inputs=identity, candidates=None, browser_inputs=None, same_source_bridge=False, reusable=lambda record: True):
    manager = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    records = list(candidates or [])
    for provider, group in baseline.get("runners", {}).items():
        for architecture, original in group.items():
            records.append(dict(original, provider=provider, architecture=architecture))
    verified = {}

    def origin(record):
        key = json.dumps(record, sort_keys=True)
        if key not in verified:
            verified[key] = verify(record)
        return verified[key]

    explained = {}
    choices = {}
    builds, reuses = [], []
    browser = {}
    browser_evidence = {}
    for architecture in artifacts.ARCHITECTURES:
        if browser_inputs:
            snapshot = browser_inputs(root, "browser-tools", architecture)
            browser_evidence[architecture] = {}
            browser[architecture] = {"action": "build", "input_identity": snapshot["input_identity"],
                                     "reason": "no matching verified browser closure", "inputs": snapshot["inputs"]}
    for provider in artifacts.PROVIDERS:
        choices[provider] = {}
        for architecture in artifacts.ARCHITECTURES:
            current = inputs(root, provider, architecture)
            native = "ubuntu-24.04-arm" if architecture == "arm64" else "ubuntu-24.04"
            decision = {"action": "build", "reason": "no qualified component with matching effective source inputs",
                        "source_revision": manager, "input_identity": current["input_identity"], "inputs": current["inputs"],
                        "candidate_decisions": []}
            compatible = [record for record in records if record.get("provider") == provider and record.get("architecture") == architecture]
            if not compatible:
                decision['reason'] = 'no discovered candidate for this provider/native architecture'
            for record in compatible:
                qualification = record.get('qualification')
                qualification = qualification if isinstance(qualification, dict) else {}
                audit = {'source_revision': record.get('source_revision'),
                         'input_identity': record.get('input_identity'),
                         'original_run_id': qualification.get('run_id'),
                         'original_artifact_id': qualification.get('artifact_id')}
                decision['candidate_decisions'].append(audit)
                matches = record.get("input_identity") == current["input_identity"]
                matches_browser = (architecture in browser and
                                   record.get("browser_input_identity") == browser[architecture]["input_identity"])
                finite=None
                if not matches and not same_source_bridge:
                    from finite_transition import evaluate_component
                    try:finite=evaluate_component(root,record,provider,architecture)
                    except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError):pass
                if not matches:
                    audit['outcome'] = 'effective_inputs_differ'
                    key = json.dumps(record, sort_keys=True)
                    if key not in explained:
                        try:
                            previous = previous_inputs(root, record)
                            if previous['input_identity'] == record.get('input_identity'):
                                explained[key] = input_delta(previous, current)
                            else:
                                explained[key] = {'input_comparison': 'original_snapshot_not_reproduced'}
                        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
                            explained[key] = {'input_comparison': 'original_source_or_recipe_unavailable'}
                    audit.update(explained[key])
                if not matches and not matches_browser and finite is None:
                    continue
                try:
                    receipt = origin(record)
                except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
                    audit.update(outcome='original_evidence_refused', refusal=evidence_refusal(error))
                    if matches:
                        decision["reason"] = "original immutable native evidence/payload unavailable or incompatible; verified-input rebuild required"
                    continue
                if matches_browser and provider not in browser_evidence[architecture]:
                    browser_evidence[architecture][provider] = {"record": record, "origin": receipt}
                if not matches and finite is None:
                    continue
                if same_source_bridge:
                    audit['outcome'] = 'explicit_same_source_bridge_requested'
                    continue
                if decision['action'] != 'build':
                    audit['outcome'] = 'compatible_original_not_selected'
                    continue
                if not reusable(record):
                    audit['outcome'] = 'production_public_reference_unavailable'
                    decision['reason'] = 'matching qualified original lacks required anonymous canonical production availability'
                    continue
                audit['outcome'] = 'selected_original'
                original = {key: value for key, value in record.items() if key not in ("provider", "architecture")}
                decision.update(action="reuse", reason="unchanged declared inputs; original qualified resolution retained",
                                source_revision=record["source_revision"], runner=original, record=record,
                                origin=receipt, verified_origin=receipt)
                if finite is not None:
                    decision.update(reason='exact finite compatible-original component selection',
                        selected_input_identity=record['input_identity'],desired_input_identity=current['input_identity'],
                        selection_mode='finite-component-v1',input_transition=finite)
            if decision['action'] == 'build' and compatible and all(
                    item['outcome'] == 'effective_inputs_differ' for item in decision['candidate_decisions']):
                decision['reason'] = 'all discovered provider/platform candidates have different effective Docker inputs'
            # A genuinely distinct prior qualified image enables real image-changing updater tests.
            for prior in compatible:
                if decision["action"] != "reuse" or prior.get("image") == decision["record"]["image"]:
                    continue
                try:
                    prior_origin = origin(prior)
                except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
                    continue
                decision.update(prior_record=prior, prior_origin=prior_origin)
                break
            choices[provider][architecture] = decision
            entry = {"provider": provider, "architecture": architecture, "native_runner": native,
                     "input_identity": current["input_identity"], "source_revision": decision["source_revision"]}
            if decision["action"] == "reuse":
                entry.update(record=decision["record"], origin=decision["origin"])
                for key in ('selection_mode','selected_input_identity','desired_input_identity','input_transition'):
                    if key in decision:entry[key]=decision[key]
                if "prior_record" in decision:
                    entry.update(prior_record=decision["prior_record"], prior_origin=decision["prior_origin"])
                reuses.append(entry)
            else:
                builds.append(entry)
    for architecture, evidence in browser_evidence.items():
        if set(evidence) == set(artifacts.PROVIDERS):
            primary = evidence["claude"]
            browser[architecture].update(action="reuse", reason="unchanged browser closure; original root and agent-user native qualifications retained",
                                         record=primary["record"], origin=primary["origin"], qualifications=evidence)
    return {"schema_version": 1, "manager_source_revision": manager, "planning_complete": True,
            "has_builds": bool(builds), "all_reuse_verified": not builds,
            "build_matrix": {"include": builds}, "reuse_matrix": {"include": reuses},
            "browser": browser, "components": choices}


def compatible_plan(root,baseline,verify=verify_origin,candidates=None):
    """The finite migration mode refuses missing evidence; it never schedules builds."""
    from finite_transition import evaluate,policy
    expected=policy();pool=list(candidates or [])
    for provider,group in baseline.get('runners',{}).items():
        for architecture,record in group.items():pool.append(dict(record,provider=provider,architecture=architecture))
    selected={};origins={};cache={}
    def checked(record):
        key=json.dumps(record,sort_keys=True)
        if key not in cache:cache[key]=verify(record)
        return cache[key]
    for provider in artifacts.PROVIDERS:
        selected[provider]={};origins[provider]={}
        for architecture in artifacts.ARCHITECTURES:
            matches=[r for r in pool if r.get('provider')==provider and r.get('architecture')==architecture and
                     r.get('source_revision')==expected['source_revision'] and
                     r.get('input_identity')==expected['source_input_identities'][provider][architecture]]
            for record in matches:
                try:origin=checked(record)
                except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError):continue
                selected[provider][architecture]=record;origins[provider][architecture]=origin;break
            artifacts.require(architecture in selected[provider], 'compatible original proof/payload unavailable; refusing migration without image builds')
    transition=evaluate(root,selected)
    records=[selected[p][a] for p in artifacts.PROVIDERS for a in artifacts.ARCHITECTURES]
    result=plan(root,{},verify=checked,candidates=records,browser_inputs=identity)
    reuses=[]
    for provider in artifacts.PROVIDERS:
        for architecture in artifacts.ARCHITECTURES:
            record=selected[provider][architecture];origin=origins[provider][architecture]
            decision=result['components'][provider][architecture]
            decision.update(action='reuse',reason='explicit finite compatible-original selection; original qualification retained',
                source_revision=record['source_revision'],selected_input_identity=record['input_identity'],
                desired_input_identity=decision['input_identity'],selection_mode='finite-input-transition-v1',
                input_transition=transition,record=record,origin=origin,verified_origin=origin,
                runner={k:v for k,v in record.items() if k not in ('provider','architecture')})
            reuses.append({'provider':provider,'architecture':architecture,'source_revision':record['source_revision'],
                'input_identity':decision['input_identity'],'selected_input_identity':record['input_identity'],
                'desired_input_identity':decision['input_identity'],'selection_mode':'finite-input-transition-v1',
                'input_transition':transition,'record':record,'origin':origin})
    result.update(has_builds=False,all_reuse_verified=True,build_matrix={'include':[]},reuse_matrix={'include':reuses},
                  release_mode='manager-envelope-v1',input_transition=transition)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--baseline", help="explicit source-bound manifest; every original component still verified")
    parser.add_argument("--baseline-run", type=int, help="exact authorized manual candidate qualification run (diagnostic plan only)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--production", action="store_true", help="reuse only anonymously available canonical production digests")
    parser.add_argument("--require-reuse", action="store_true")
    parser.add_argument("--compatible-originals",action="store_true",help="finite verified original selection; missing evidence refuses without builds")
    parser.add_argument("--same-source-bridge", action="store_true", help="qualify all four real current-source schema1 bridge images")
    args = parser.parse_args()
    try:
        artifacts.require(not (args.baseline and args.baseline_run), "choose one baseline selector")
        if args.baseline_run is not None:
            artifacts.require(args.baseline_run > 0 and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
                              os.environ.get("GITHUB_REPOSITORY") == "Jayson-Stangel/symphony",
                              "explicit pre-merge baseline requires a manual fork diagnostic")
        baseline = {"runners": {}}
        candidates = None
        if args.baseline:
            baseline = json.loads(Path(args.baseline).read_text())
            artifacts.validate(baseline, require_bundle=False)
        else:
            candidates = discover(candidate_run=args.baseline_run)
        if args.baseline_run is not None:
            from component_selection import manual_verifier
            verifier=manual_verifier(args.baseline_run)
            verifier.validate_candidates(candidates or [])
        else:verifier=verify_origin
        artifacts.require(not args.same_source_bridge,'forced same-source image bridge retired; select compatible originals explicitly')
        reusable = lambda record: True
        if args.production:
            artifacts.require(not args.compatible_originals and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
                              and os.environ.get('GITHUB_REF') == 'refs/heads/main', 'production planning requires manual main')
            from registry_closure import registry_available
            def reusable(record):
                return registry_available('ghcr.io/jayson-stangel/symphony-' + record['provider'] + '@' + record['image'].split('@', 1)[1])
        if args.compatible_originals:result=compatible_plan(args.root,baseline,verify=verifier,candidates=candidates)
        else:result=plan(args.root,baseline,verify=verifier,candidates=candidates,browser_inputs=identity,reusable=reusable)
        result["baseline_selection"] = {"mode": "explicit-manual-candidate" if args.baseline_run else
                                       "explicit-manifest" if args.baseline else "bounded-verified-fork-history",
                                       "run_id": args.baseline_run, "candidate_count": len(candidates or [])}
        if args.baseline_run is not None:
            result["baseline_selection"]["joined_anchor"]=verifier.selection_anchor()
        Path(args.output).write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        if args.require_reuse and result["has_builds"]:
            parser.exit(1, "runner reuse: at least one component requires a verified-input rebuild; plan retained\n")
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"runner reuse: {type(error).__name__}; no reuse decision was authorized\n")


if __name__ == "__main__":
    main()
