#!/usr/bin/env bash
# GHA also enforces ref visibility: PR merge-ref caches cannot replace base caches.
set -euo pipefail
case "${TARGET:?}" in codex|claude|browser-tools) ;; *) echo 'unsupported cached target' >&2; exit 1 ;; esac
case "$(uname -m)" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) echo 'unsupported native cache architecture' >&2; exit 1 ;; esac
case "${EVENT:?}/${REF:?}" in
  pull_request/*)
    [[ "${PR_NUMBER:-}" =~ ^[0-9]+$ ]] || { echo 'missing PR identity' >&2; exit 1; }
    namespace="pr-$PR_NUMBER"
    ;;
  push/refs/heads/dev|push/refs/heads/main|workflow_dispatch/refs/heads/dev|workflow_dispatch/refs/heads/main)
    namespace=trusted
    ;;
  *) namespace="ref-$(printf '%s' "$REF" | shasum -a 256 | cut -c1-20)" ;;
esac
base_namespace=$namespace
case "${CACHE_VARIANT:-}" in
  '') ;;
  changed) namespace="$namespace-changed" ;;
  *) echo 'unsupported diagnostic cache variant' >&2; exit 1 ;;
esac
printf 'base_namespace=%s\nnamespace=%s\narch=%s\n' "$base_namespace" "$namespace" "$arch" >> "${GITHUB_OUTPUT:?}"
printf '### Build layer cache\nTarget: `%s`; native platform: `linux/%s`; selected namespace: `%s`; export enabled: `%s`.\nLayer hits remain subject to exact Dockerfile, base, COPY, lockfile and build-argument keys. Missing caches build cold.\n' "$TARGET" "$arch" "$namespace" "${CACHE_EXPORT:-true}" >> "${GITHUB_STEP_SUMMARY:?}"
