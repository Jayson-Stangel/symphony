# Sourced by the packaged launcher. Imports only the manifest-selected candidate.
package_runner_import() (
  set -euo pipefail
  umask 077
  local agent=${1:?provider required} archive=${2:?archive required} runtime image temporary owner identifier context endpoint
  case "$agent" in codex|claude) ;; *) die 'runner import requires codex or claude' ;; esac
  [ "$package_channel" = candidate ] || die 'runner import is only for candidate packages; stable releases use their published registry'
  runtime=$(container_runtime)
  "$runtime" info >/dev/null 2>&1 || die 'runner import: container engine unavailable; start Docker Desktop/OrbStack or check the selected Docker context'
  context=$("$runtime" context show) || die 'runner import: cannot identify Docker context'
  endpoint=$("$runtime" context inspect "$context" --format '{{.Endpoints.docker.Host}}') || die 'runner import: cannot inspect Docker context'
  if [ -z "${DOCKER_CONTEXT:-}" ] && [ -n "${DOCKER_HOST:-}" ]; then endpoint=$DOCKER_HOST; fi
  case "$endpoint" in unix://*) ;; *) die 'runner import requires a local Unix-socket Docker context for its verified read-only bind mount' ;; esac
  image=$(package_manifest_image "$agent")
  case "$image" in "localhost:5000/symphony-$agent@sha256:"*) ;; *) die 'candidate uses a registry distribution; import is unnecessary or unsupported for this reference' ;; esac
  # A registry is an explicit Docker prerequisite, not a host compiler/runtime.
  "$runtime" image inspect registry:3 >/dev/null 2>&1 || "$runtime" pull registry:3 || die 'runner import needs registry:3; pull it through Docker before retrying'
  temporary=$(mktemp -d "${TMPDIR:-/tmp}/symphony-runner-import.XXXXXXXX")
  owner=${temporary##*/}
  identifier=
  remove_import_registry() {
    local observed
    # The cidfile survives a signal between Docker create and shell assignment.
    if [ -z "$identifier" ] && [ -s "$temporary/container-id" ]; then identifier=$(cat "$temporary/container-id"); fi
    if [ -n "$identifier" ]; then
      [[ "$identifier" =~ ^[a-f0-9]{64}$ ]] || { printf 'symphony: invalid candidate registry ownership receipt; retained %s\n' "$temporary" >&2; return 1; }
      observed=$("$runtime" inspect --format '{{ index .Config.Labels "com.symphony.candidate-import" }}' "$identifier") || {
        printf 'symphony: candidate registry cleanup could not verify ownership; retained %s\n' "$temporary" >&2; return 1;
      }
      [ "$observed" = "$owner" ] || { printf 'symphony: candidate registry ownership differs; cleanup refused; retained %s\n' "$temporary" >&2; return 1; }
      "$runtime" rm --force --volumes "$identifier" >/dev/null || { printf 'symphony: candidate registry cleanup failed; retained %s\n' "$temporary" >&2; return 1; }
      identifier=
      rm -f "$temporary/container-id"
    fi
  }
  cleanup_import() {
    local result=$?
    trap - EXIT INT TERM
    remove_import_registry || exit 1
    rm -rf "$temporary"
    exit "$result"
  }
  trap cleanup_import EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  package_helper prepare-runner-import "$symphony_home/release.json" "$agent" "$package_arch" "$archive" "$temporary" >/dev/null || die 'candidate archive verification failed; obtain the exact companion payload for this installed package'
  local allocation acquired=false binding transport
  # The registry binds port zero in the local daemon network namespace. The kernel
  # allocates the endpoint without a probe/release race or a host-published port.
  # Retry only our own failed acquisition, bounded even if engine allocation races.
  for allocation in 1 2 3; do
    identifier=$("$runtime" create --name "$owner" --cidfile "$temporary/container-id" --label "com.symphony.candidate-import=$owner" \
      --network host --env REGISTRY_HTTP_ADDR=127.0.0.1:0 \
      --env REGISTRY_HTTP_DEBUG_ADDR=127.0.0.1:0 --env REGISTRY_LOG_FORMATTER=json --mount "type=bind,src=$temporary/registry,dst=/var/lib/registry,readonly" registry:3) || die 'cannot acquire candidate registry; check local Docker bind-mount access; no existing resource was replaced'
    [[ "$identifier" =~ ^[a-f0-9]{64}$ ]] || die 'invalid candidate registry acquisition receipt'
    if "$runtime" start "$identifier" >/dev/null; then acquired=true; break; fi
    remove_import_registry || exit 1
  done
  [ "$acquired" = true ] || die 'candidate registry endpoint allocation failed after 3 attempts; check local Docker host-network support and bind-mount access, then retry'
  # Wait for this exact container, never a different service already on the port.
  local attempt ready=false
  for attempt in {1..30}; do
    [ "$("$runtime" inspect --format '{{.State.Running}}' "$identifier")" = true ] || die 'candidate registry stopped before import'
    binding=$("$runtime" logs --tail 100 "$identifier" 2>&1 | package_helper runner-import-endpoint 2>/dev/null) || binding=
    if [ -n "$binding" ] && "$runtime" exec "$identifier" wget -T 2 -q -O /dev/null "http://$binding/v2/"; then ready=true; break; fi
    sleep 1
  done
  [ "$ready" = true ] || die 'candidate registry did not become ready; check local Docker host-network loopback support and retry'
  transport="$binding/${image#localhost:5000/}"
  "$runtime" pull "$transport" || die 'cannot pull the verified candidate graph; the temporary registry was not substituted for another environment'
  package_release_image "$runtime" "$transport" "$agent" "$package_revision"
  package_helper store-runner-import "$symphony_home/release.json" "$agent" "$package_arch" "$package_app_home" "$transport" >/dev/null || die 'cannot retain verified candidate transport reference'
  printf 'Imported exact %s candidate image: %s\nFor manual recovery after pruning, repeat runner import with the same companion.\n' "$agent" "$image"
)

package_runner_import_info() {
  local agent=${1:-codex} image field value
  case "$agent" in codex|claude) ;; *) die 'runner import requires codex or claude' ;; esac
  image=$(package_manifest_image "$agent")
  printf 'Selected %s runner (%s): %s\n' "$agent" "$package_arch" "$image"
  case "$package_channel/$image" in
    "candidate/localhost:5000/symphony-$agent@sha256:"*)
      printf 'Original companion for manual or offline recovery from GitHub Actions:\n'
      for field in run_id artifact_id artifact_name archive_filename archive_sha256; do
        value=$(package_helper release-get "$symphony_home/release.json" "runners.$agent.$package_arch.qualification.$field") || die 'candidate is missing its original companion identity'
        printf '  %s: %s\n' "$field" "$value"
        if [ "$field" = run_id ]; then printf '  URL: https://github.com/Jayson-Stangel/symphony/actions/runs/%s\n' "$value"; fi
      done
      printf 'Prerequisites: local Docker engine, registry:3 image, space for archive plus extracted graph and Docker image.\n'
      printf 'Extract the downloaded artifact ZIP, then run:\n  symphony runner import --agent %s --archive /absolute/path/to/%s-%s.docker.tar.gz\n' "$agent" "$agent" "$package_arch"
      ;;
    *) printf 'This selected image uses its registry distribution; candidate import is unnecessary or unsupported.\n' ;;
  esac
}
