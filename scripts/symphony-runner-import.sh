# Sourced by the packaged launcher. Imports only the manifest-selected candidate.
package_runner_import() (
  set -euo pipefail
  local agent=${1:?provider required} archive=${2:?archive required} runtime image temporary owner identifier inspected context endpoint
  case "$agent" in codex|claude) ;; *) die 'runner import requires codex or claude' ;; esac
  [ "$package_channel" = candidate ] || die 'runner import is only for candidate packages; stable releases use their published registry'
  runtime=$(container_runtime)
  "$runtime" info >/dev/null 2>&1 || die 'runner import: container engine unavailable; start Docker Desktop/OrbStack or check the selected Docker context'
  context=$("$runtime" context show) || die 'runner import: cannot identify Docker context'
  endpoint=$("$runtime" context inspect "$context" --format '{{.Endpoints.docker.Host}}') || die 'runner import: cannot inspect Docker context'
  if [ -z "${DOCKER_CONTEXT:-}" ] && [ -n "${DOCKER_HOST:-}" ]; then endpoint=$DOCKER_HOST; fi
  case "$endpoint" in unix://*) ;; *) die 'runner import requires a local Unix-socket Docker context for its verified read-only bind mount' ;; esac
  image=$(package_image "$agent")
  case "$image" in "localhost:5000/symphony-$agent@sha256:"*) ;; *) die 'candidate uses a registry distribution; import is unnecessary or unsupported for this reference' ;; esac
  # A registry is an explicit Docker prerequisite, not a host compiler/runtime.
  "$runtime" image inspect registry:3 >/dev/null 2>&1 || "$runtime" pull registry:3 || die 'runner import needs registry:3; pull it through Docker before retrying'
  temporary=$(mktemp -d "${TMPDIR:-/tmp}/symphony-runner-import.XXXXXXXX")
  owner=${temporary##*/}
  identifier=
  cleanup_import() {
    local result=$? observed
    trap - EXIT INT TERM
    if [ -n "$identifier" ]; then
      observed=$("$runtime" inspect --format '{{ index .Config.Labels "com.symphony.candidate-import" }}' "$identifier") || {
        printf 'symphony: candidate registry cleanup could not verify ownership; retained %s\n' "$temporary" >&2; exit 1;
      }
      [ "$observed" = "$owner" ] || { printf 'symphony: candidate registry ownership differs; cleanup refused\n' >&2; exit 1; }
      "$runtime" rm --force --volumes "$identifier" >/dev/null || { printf 'symphony: candidate registry cleanup failed; retained %s\n' "$temporary" >&2; exit 1; }
    fi
    rm -rf "$temporary"
    exit "$result"
  }
  trap cleanup_import EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  package_helper prepare-runner-import "$symphony_home/release.json" "$agent" "$package_arch" "$archive" "$temporary" >/dev/null || die 'candidate archive verification failed; obtain the exact companion payload for this installed package'
  identifier=$("$runtime" create --name "$owner" --label "com.symphony.candidate-import=$owner" \
    -p 127.0.0.1:5000:5000 --mount "type=bind,src=$temporary/registry,dst=/var/lib/registry,readonly" registry:3) || die 'cannot acquire candidate registry; no existing resource was replaced'
  [[ "$identifier" =~ ^[a-f0-9]{64}$ ]] || die 'invalid candidate registry acquisition receipt'
  "$runtime" start "$identifier" >/dev/null || die 'candidate registry cannot bind localhost:5000; stop the conflicting service yourself or use the selected registry distribution'
  # Wait for this exact container, never a different service already on the port.
  local attempt ready=false
  for attempt in {1..30}; do
    [ "$("$runtime" inspect --format '{{.State.Running}}' "$identifier")" = true ] || die 'candidate registry stopped before import'
    if "$runtime" exec "$identifier" wget -q -O /dev/null http://127.0.0.1:5000/v2/; then ready=true; break; fi
    sleep 1
  done
  [ "$ready" = true ] || die 'candidate registry did not become ready; retry after inspecting Docker availability'
  "$runtime" pull "$image" || die 'cannot pull the verified candidate graph; the temporary registry was not substituted for another environment'
  package_release_image "$runtime" "$image" "$agent" "$package_revision"
  printf 'Imported exact %s candidate image: %s\nRepeat runner import if Docker later prunes this digest.\n' "$agent" "$image"
)

package_runner_import_info() {
  local agent=${1:-codex} image field value
  case "$agent" in codex|claude) ;; *) die 'runner import requires codex or claude' ;; esac
  image=$(package_image "$agent")
  printf 'Selected %s runner (%s): %s\n' "$agent" "$package_arch" "$image"
  case "$package_channel/$image" in
    "candidate/localhost:5000/symphony-$agent@sha256:"*)
      printf 'Before login, download this exact original companion from GitHub Actions:\n'
      for field in run_id artifact_id artifact_name archive_filename archive_sha256; do
        value=$(package_helper release-get "$symphony_home/release.json" "runners.$agent.$package_arch.qualification.$field") || die 'candidate is missing its original companion identity'
        printf '  %s: %s\n' "$field" "$value"
        if [ "$field" = run_id ]; then printf '  URL: https://github.com/Jayson-Stangel/symphony/actions/runs/%s\n' "$value"; fi
      done
      printf 'Prerequisites: local Docker engine, registry:3 image, free localhost:5000, space for archive plus extracted graph and Docker image.\n'
      printf 'Extract the downloaded artifact ZIP, then run:\n  symphony runner import --agent %s --archive /absolute/path/to/%s-%s.docker.tar.gz\n' "$agent" "$agent" "$package_arch"
      ;;
    *) printf 'This selected image uses its registry distribution; candidate import is unnecessary or unsupported.\n' ;;
  esac
}
