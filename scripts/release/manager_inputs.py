#!/usr/bin/env python3
"""Remeasure selected schema2 runner inputs at manager assembly boundaries."""
import argparse
from pathlib import Path
import subprocess

import artifacts
from finite_transition import evaluate_component
from manager_envelope import load_release
from runner_inputs import identity


def validate_checkout(root,manifest):
    root=Path(root).resolve()
    revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    artifacts.require(manifest.get('source_revision')==revision,'manager manifest differs from actual checkout')
    artifacts.validate(manifest,require_bundle=False)
    if manifest['schema_version']==1:return manifest
    if manifest['compatibility'].get('selection_mode')=='runner-protocol-v1':
        # Original runtime inputs remain original. Host manager compatibility
        # does not claim this checkout was baked into those unchanged images.
        return manifest
    for provider in artifacts.PROVIDERS:
        for architecture in artifacts.ARCHITECTURES:
            record=manifest['runners'][provider][architecture]
            actual=identity(root,provider,architecture)['input_identity']
            receipt=manifest.get('component_selections',{}).get(provider,{}).get(architecture)
            if receipt is None:
                artifacts.require(record['input_identity']==actual,'selected exact runner inputs differ from actual manager checkout')
            else:
                artifacts.require(evaluate_component(root,record,provider,architecture)==receipt,
                                  'finite runner inputs differ from measured component authority')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path);parser.add_argument('--root',type=Path,default=Path('.'))
    args=parser.parse_args()
    try:validate_checkout(args.root,load_release(args.manifest))
    except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError) as error:
        parser.exit(1,'manager input selection refused: '+str(error)+'\n')

if __name__=='__main__':main()
