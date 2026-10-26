# Sourced by the packaged launcher. Acquisition never selects a project environment.
package_runner_provision() (
  set -euo pipefail
  set +x
  local agent=${1:?provider required} image proof root credentials token artifact temporary size
  image=$(package_helper release-get "$symphony_home/release.json" "runners.$agent.$package_arch.image")
  case "$package_channel/$image" in "candidate/localhost:5000/symphony-$agent@sha256:"*) ;; *) die 'automatic companion acquisition requires an original candidate graph' ;; esac
  root=$(repo_root)
  credentials="$config_home/projects/$(project_id "$root").env"
  # JAY-738 supplies the shared literal reader in the composed launcher. Keep
  # standalone candidate acquisition compatible with the generated file format,
  # without evaluating credential files as shell programs.
  if declare -F project_credentials_read >/dev/null; then
    project_credentials_read "$root" || return
    token=$credential_git
  else
    [ -r "$credentials" ] || die "candidate download needs the GitHub credential in $credentials; run symphony init and fill GH_TOKEN (repository Actions read access), then retry login"
    package_helper owned-file "$credentials" || return
    local line key value github_token=
    token=
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in GH_TOKEN=*|GITHUB_TOKEN=*)
        key=${line%%=*}; value=${line#*=}
        case "$value" in \"*\") value=${value#\"}; value=${value%\"} ;; \'*\') value=${value#\'}; value=${value%\'} ;; esac
        if [ "$key" = GH_TOKEN ]; then token=$value; else github_token=$value; fi
        ;;
      esac
    done < "$credentials"
    token=${token:-$github_token}
  fi
  [[ "$token" =~ ^[A-Za-z0-9_]+$ ]] || die "candidate download needs GH_TOKEN with repository Actions read access in $credentials; native provider login is separate"
  command -v curl >/dev/null || die 'candidate download requires the ordinary curl runtime utility with CA certificates'
  artifact=$(package_helper release-get "$symphony_home/release.json" "runners.$agent.$package_arch.qualification.artifact_id")
  [[ "$artifact" =~ ^[0-9]+$ ]] || die 'candidate is missing its original artifact identity'
  temporary=$(mktemp -d "${TMPDIR:-/tmp}/symphony-runner-download.XXXXXXXX")
  trap 'rm -rf "$temporary"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  candidate_request() {
    # curl drops Authorization on cross-host redirects; never use --location-trusted.
    printf 'header = "Authorization: Bearer %s"\n' "$token" |
      curl -q --config - --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 20 --max-time 7200 --retry 2 --retry-delay 1 \
        --header 'Accept: application/vnd.github+json' --header 'X-GitHub-Api-Version: 2022-11-28' \
        --max-filesize "$2" --output "$3" "https://api.github.com/repos/Jayson-Stangel/symphony/actions/artifacts/$artifact$1"
  }
  printf 'Checking access to the exact %s/%s companion (artifact %s). Candidate downloads require repository Actions read access.\n' "$agent" "$package_arch" "$artifact" >&2
  candidate_request '' 1048576 "$temporary/metadata.json" || die 'selected candidate artifact is inaccessible; check the project GitHub credential Actions read permission or choose an available reviewed candidate. Offline recovery: symphony runner import --agent PROVIDER --archive PATH'
  size=$(package_helper validate-candidate-artifact "$symphony_home/release.json" "$agent" "$package_arch" "$temporary/metadata.json") || return
  printf 'Downloading sealed original companion (%s bytes); allow space for ZIP, graph archive, extracted graph and Docker image. Interrupted downloads can be retried with the same login command.\n' "$size" >&2
  candidate_request /zip "$size" "$temporary/artifact.zip" || die 'candidate download failed; check network and free disk space, then retry login. No project environment was selected'
  unset token
  printf 'Verifying exact artifact and original graph archive.\n' >&2
  package_helper extract-candidate-artifact "$symphony_home/release.json" "$agent" "$package_arch" "$temporary/artifact.zip" "$temporary/runner.docker.tar.gz" || return
  rm "$temporary/artifact.zip"
  . "$symphony_home/scripts/symphony-runner-import.sh"
  package_runner_import "$agent" "$temporary/runner.docker.tar.gz" >&2
)

package_ensure_candidate_image() {
  local agent=$1 image runtime
  image=$(package_image "$agent")
  case "$package_channel/$image" in
    candidate/localhost:5000/*@sha256:*|candidate/127.0.0.1:*/*@sha256:*)
      runtime=$(container_runtime)
      container_engine_ready "$runtime" || return
      if ! "$runtime" image inspect "$image" >/dev/null 2>&1; then
        package_runner_provision "$agent" || return
      fi
      ;;
  esac
}
