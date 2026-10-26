"""Synthetic immutable settings preconditions for composed launcher fixtures.

Use the actual manager store/require functions. No native account is inspected.
"""
import hashlib
import json

EMPTY_SNAPSHOT = json.dumps({
    "schema": 1, "mode": "codex-config-snapshot", "profile": "trust-ro-v2",
    "config_base64": "", "sha256": hashlib.sha256(b"").hexdigest(),
    "source": {"present": False},
})

PIN_SETTINGS = r'''
runner_storage_id() { printf fixture-storage; }
fixture_pin_codex_settings() {
  local store
  store=$(codex_config_store_root "$1")
  codex_config_sha=$(printf '%s' "$FIXTURE_EMPTY_SNAPSHOT" | codex_config_store write "$store") || return
  codex_config_owner=$(id -u)
  codex_config_file="$store/$codex_config_sha/config.toml"
  codex_config_image=$2
  codex_config_require "$1" "$2"
}
'''
