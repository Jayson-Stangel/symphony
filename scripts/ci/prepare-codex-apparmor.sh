#!/usr/bin/env bash
# Explicit administrator preparation for disposable GitHub-hosted Linux CI only.
# Product launchers never install host policy. This does not qualify an image.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
mode=${1:?prepare or cleanup required}
case "$mode" in prepare|cleanup) ;; *) echo 'CI AppArmor: expected prepare or cleanup' >&2; exit 1 ;; esac
fail() { echo "CI AppArmor: $*" >&2; exit 1; }
[ "${GITHUB_ACTIONS:-}" = true ] && [ "${RUNNER_ENVIRONMENT:-}" = github-hosted ] && [ "$(uname -s)" = Linux ] || fail 'administrator preparation requires a disposable GitHub-hosted Linux runner'
[ -n "${RUNNER_TEMP:-}" ] && [ -d "$RUNNER_TEMP" ] || fail 'RUNNER_TEMP is unavailable'
state="$RUNNER_TEMP/symphony-codex-apparmor"
# Docker context selection takes precedence over DOCKER_HOST. Resolve the actual
# endpoint, but never print it: an arbitrary endpoint can contain credentials.
if [ -n "${DOCKER_CONTEXT:-}" ]; then
  endpoint=$(docker context inspect "$DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}' 2>/dev/null) || fail 'cannot inspect selected Docker context'
elif [ -n "${DOCKER_HOST:-}" ]; then
  endpoint=$DOCKER_HOST
else
  endpoint=$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null) || fail 'cannot inspect selected Docker context'
fi
[ "$endpoint" = unix:///var/run/docker.sock ] || fail 'selected Docker daemon is not the local hosted socket; prepare policy on its actual host'
[ "$(docker info --format '{{.Name}}' 2>/dev/null)" = "$(hostname)" ] || fail 'Docker daemon host differs from the disposable controller'
[ "$(docker info --format '{{.OSType}}' 2>/dev/null)" = linux ] || fail 'selected daemon is not Linux'
SYMPHONY_HOME="$root" source "$root/scripts/symphony" --help >/dev/null
profile=$(codex_apparmor_profile docker 2>/dev/null) || fail 'cannot select shipped Docker security integration'
if [ "$profile" = none ]; then
  printf 'CI AppArmor: selected daemon does not advertise AppArmor; shipped no-AppArmor integration retained\n'
  exit 0
fi
policy="$root/scripts/codex-apparmor.profile"
codex_verify_profile "$policy" "$(codex_apparmor_id)"
# A loaded name alone does not identify its contents. Never replace or remove a
# preexisting profile: this invocation owns only a newly added exact policy.
profiles() { sudo -n cat /sys/kernel/security/apparmor/profiles; }
loaded() { profiles | LC_ALL=C grep -Fx "$profile (enforce)" >/dev/null; }
if [ "$mode" = cleanup ]; then
  [ -e "$state" ] || { echo 'CI AppArmor: no owned policy to clean up'; exit 0; }
  [ -f "$state" ] && [ ! -L "$state" ] || fail 'unsafe policy ownership receipt'
  [ "$(cat "$state")" = "$profile:$(codex_apparmor_id)" ] || fail 'policy ownership receipt differs from shipped identity'
  remaining=$(profiles) || fail 'cannot inspect owned profile during cleanup'
  if ! printf '%s\n' "$remaining" | LC_ALL=C grep -E "^$profile \\(" >/dev/null; then
    rm "$state"
    echo 'CI AppArmor: failed preparation left no loaded owned profile'
    exit 0
  fi
  sudo -n apparmor_parser --remove "$policy" || fail 'cannot remove owned policy; ephemeral runner cleanup failed'
  remaining=$(profiles) || fail 'cannot verify profile removal; inspect ephemeral host cleanup'
  if printf '%s\n' "$remaining" | LC_ALL=C grep -E "^$profile \\(" >/dev/null; then fail 'owned profile remains loaded after cleanup'; fi
  rm "$state"
  printf 'CI AppArmor: owned profile removed: %s\n' "$profile"
  exit 0
fi
[ ! -e "$state" ] && [ ! -L "$state" ] || fail 'policy ownership receipt already exists'
printf 'CI AppArmor: daemon=local AppArmor=advertised kernel=%s architecture=%s\n' "$(uname -r)" "$(uname -m)"
[ "$(cat /sys/module/apparmor/parameters/enabled 2>/dev/null)" = Y ] || fail 'daemon advertises AppArmor but host kernel AppArmor is unavailable'
lsm=$(cat /sys/kernel/security/lsm 2>/dev/null) || fail 'host LSM inventory unavailable; check securityfs and kernel support'
case ",$lsm," in *,apparmor,*) ;; *) fail 'AppArmor is absent from the active host LSM inventory' ;; esac
stat /proc/thread-self/attr/apparmor/exec >/dev/null 2>&1 || fail 'AppArmor per-thread exec interface unavailable; kernel/runtime is incompatible'
existing=$(profiles) || fail 'cannot inspect loaded profiles; require noninteractive administrator access'
if printf '%s\n' "$existing" | LC_ALL=C grep -E "^$profile \\(" >/dev/null; then fail 'named profile already exists; refusing to replace policy with unknown provenance'; fi
if ! command -v apparmor_parser >/dev/null; then
  echo 'CI AppArmor: installing the policy parser on the verified disposable daemon host'
  sudo -n apt-get update && sudo -n apt-get install -y --no-install-recommends apparmor || fail 'cannot install AppArmor parser on disposable host'
fi
command -v apparmor_parser >/dev/null || fail 'apparmor_parser remains unavailable after disposable-host preparation'
# Record ownership before mutation so always() cleanup handles a parser that
# adds the profile but subsequently reports an error.
( set -o noclobber; printf '%s\n' "$profile:$(codex_apparmor_id)" > "$state" )
sudo -n apparmor_parser --add "$policy" || fail 'exact shipped policy could not be loaded; inspect kernel/parser compatibility'
loaded || fail 'exact shipped named profile is not in enforce mode after loading'
printf 'CI AppArmor: kernel=%s architecture=%s source=%s\n' "$(uname -r)" "$(uname -m)" "$(git -C "$root" rev-parse HEAD)"
printf 'CI AppArmor: profile=%s sha256=%s loaded=enforce; actual immutable-image protected qualification remains required\n' "$profile" "$(codex_apparmor_id)"
