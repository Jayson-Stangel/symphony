# Sourced only by a release bundle. Lifecycle state continues to belong to the launcher.
packaged_install=true
[ -z "${SYMPHONY_RUNNER_IMAGE:-}" ] || die 'packaged runner images are selected by the release and installed updater; remove SYMPHONY_RUNNER_IMAGE'
symphony_home=$(cd "$symphony_home" && pwd -P)
package_helper() { "$symphony_home/libexec/symphony-host" "$@"; }
package_helper validate-manifest "$symphony_home/release.json" || exit 1
package_version=$(package_helper get "$symphony_home/release.json" version)
package_revision=$(package_helper get "$symphony_home/release.json" source_revision)
[ "$(package_helper build-revision)" = "$package_revision" ] || die 'bundled helper does not match release source revision'
package_channel=$(package_helper get "$symphony_home/release.json" channel)
case "$(uname -s)" in Darwin) ;; *) die 'packaged installs support macOS only' ;; esac
case "$(uname -m)" in arm64|aarch64) package_arch=arm64 ;; x86_64) package_arch=x86_64 ;; *) die 'unsupported host architecture; select an ARM64 or Intel macOS release' ;; esac
package_app_home=${SYMPHONY_APP_HOME:-"$HOME/.local/share/$runtime_prefix"}
package_app_home=$(package_helper canonical-path "$package_app_home")
case "$symphony_home" in */releases/*) package_app_home=$(cd "$symphony_home/../.." && pwd) ;; esac

package_usage() {
  cat <<'USAGE'
Usage:
  symphony --version
  symphony doctor [--json]
  symphony init --linear-project SLUG [--local-skill NAME]...
  symphony auth codex|claude login|check|remove
  symphony run [--agent codex|claude] [--port PORT]
  symphony runner start|stop|status|doctor|logs|drain|resume|recheck
  symphony update [--agent codex|claude | --rollback-runner]

This macOS release bundle requires Docker Desktop or OrbStack. No host build or
agent toolchain is needed. From an existing project checkout, initialize it, edit
the printed private credentials file, authenticate the native container login,
and run. Runner images are pulled by immutable release digest; they never build
locally. Use the installed updater for release changes and runner recovery.
See the packaged installation guide for isolated candidate storage and namespaces.
USAGE
}

package_image() { package_helper get "$symphony_home/release.json" "runners.${1:-codex}.$package_arch.image"; }
package_release_image() {
  local runtime=$1 image=$2 agent=$3 revision=$4 platform
  [ "$image" = "$(package_image "$agent")" ] || die 'runner image is outside the selected release manifest'
  platform=$(package_helper get "$symphony_home/release.json" "runners.$agent.$package_arch.platform")
  if ! "$runtime" image inspect "$image" >/dev/null 2>&1; then
    "$runtime" pull --platform "$platform" "$image" || die "cannot pull released $agent image; check engine network access and release availability: $image"
  fi
  [ "$("$runtime" image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")" = "$revision" ] || die 'runner source revision does not match selected release'
  [ "$("$runtime" image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = "$platform" ] || die 'runner architecture does not match selected release'
  [ "$("$runtime" image inspect --format '{{ index .Config.Labels "com.symphony.agent" }}' "$image")" = "$agent" ] || die 'runner provider does not match selected release'
}
package_build_image() {
  local agent=${1:-codex} runtime image
  runtime=$(container_runtime); image=$(runner_image "$agent")
  case "$image" in
    sha256:*)
      # An already-retained project image is pinned by the existing runner metadata.
      "$runtime" image inspect "$image" >/dev/null 2>&1 || die 'retained runner image is missing; recover through symphony update'
      [ "$("$runtime" image inspect --format '{{ index .Config.Labels "com.symphony.agent" }}' "$image")" = "$agent" ] || die 'retained runner provider differs'
      ;;
    *) package_release_image "$runtime" "$image" "$agent" "$package_revision" ;;
  esac
}

# A shared manager update must not silently change another project's retained image.
package_use_project_image() {
  local root=$1 agent=$2 metadata image revision
  [ -z "${SYMPHONY_RUNNER_IMAGE:-}" ] || return 0
  metadata=$(runner_env_file "$root")
  [ -f "$metadata" ] || return 0
  package_helper owned-file "$metadata" || return
  image=$(sed -n 's/^SYMPHONY_RUNNER_IMAGE_ID=//p' "$metadata")
  case "$image" in sha256:*)
    [[ "$image" =~ ^sha256:[a-f0-9]{64}$ ]] || die 'retained runner image identity is invalid'
    [ "$(sed -n 's/^SYMPHONY_RUNNER_AGENT=//p' "$metadata")" = "$agent" ] || return 0
    export SYMPHONY_RUNNER_IMAGE=$image
    ;;
  esac
}

package_stage_bundle() {
  local source=$1 destination stage version revision
  package_helper validate-manifest "$source/release.json" || return
  version=$(package_helper get "$source/release.json" version) || return
  revision=$(package_helper get "$source/release.json" source_revision) || return
  package_helper verify-files "$source" || return
  destination="$package_app_home/releases/$version-$revision"
  # Validate each owned storage boundary before following it for staging.
  # mkdir-p would follow an existing (including dangling) releases symlink.
  package_helper owned-directory "$package_app_home" || return
  package_helper lock-directory "$package_app_home/releases" || return
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    [ ! -L "$destination" ] && [ -d "$destination" ] || die "conflicting release path: $destination"
    package_helper owned-directory "$destination" || return
    cmp -s "$source/release.json" "$destination/release.json" || die 'same release identity has different metadata'
    cmp -s "$source/files.sha256" "$destination/files.sha256" || die 'same release identity has different bundle content'
    package_helper verify-files "$destination" || return
  else
    stage=$(mktemp -d "$package_app_home/releases/.stage.XXXXXX") || return
    cp -R "$source/." "$stage/" || { rm -rf "$stage"; return 1; }
    package_helper verify-files "$stage" || { rm -rf "$stage"; return 1; }
    mv "$stage" "$destination" || { rm -rf "$stage"; return 1; }
  fi
  package_helper owned-directory "$destination" || return
  package_helper verify-files "$destination" || return
  printf '%s\n' "$destination"
}

package_preflight_links() {
  local source target skill
  source="$package_app_home/current/scripts/symphony"; target="$bin_dir/symphony"
  if [ -L "$target" ]; then [ "$(readlink "$target")" = "$source" ] || die "refusing to replace existing command link: $target"
  elif [ -e "$target" ]; then die "refusing to replace existing command: $target"; fi
  for skill in "${managed_skills[@]}"; do
    source="$package_app_home/current/.codex/skills/$skill"; target=$(global_skill_path "$skill")
    if [ -L "$target" ]; then [ "$(readlink "$target")" = "$source" ] || die "refusing to replace existing global skill link: $target"
    elif [ -e "$target" ]; then die "refusing to replace existing global skill: $target"; fi
  done
  if [ -e "$package_app_home/current" ] || [ -L "$package_app_home/current" ]; then
    [ -L "$package_app_home/current" ] || die 'application current path is not a managed release link'
    [ "$(cd "$package_app_home/current" && pwd -P)" = "$package_app_home/releases/$package_version-$package_revision" ] || die 'another version is installed; use symphony update'
  fi
}

package_install() (
  local stage_only=false skill source target destination next app_lock
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --bin-dir|--config-dir|--skills-dir|--app-dir)
        [ "$#" -ge 2 ] || die "$1 requires a path"
        case "$1" in --bin-dir) bin_dir=$2 ;; --config-dir) config_home=$2 ;; --skills-dir) skills_dir=$2 ;; --app-dir) package_app_home=$2 ;; esac; shift 2 ;;
      --stage-only) stage_only=true; shift ;;
      --help|-h) printf 'Usage: install.sh [--app-dir PATH] [--bin-dir PATH] [--config-dir PATH] [--skills-dir PATH] [--stage-only]\n'; return ;;
      *) die "unknown install option: $1" ;;
    esac
  done
  case "$package_app_home" in /*) ;; *) die 'application storage must be an absolute path' ;; esac
  package_app_home=$(package_helper canonical-path "$package_app_home")
  if ! "$stage_only"; then package_preflight_links; fi
  (umask 077; mkdir -p "$package_app_home") || return
  package_helper lock-directory "$package_app_home" || return
  app_lock="$package_app_home/update.lock"
  [ ! -L "$app_lock" ] || die 'unsafe application updater lock'
  exec 8>>"$app_lock" || return
  package_helper lock "$app_lock" 8 acquire || return
  if ! "$stage_only"; then package_preflight_links; fi
  destination=$(package_stage_bundle "$symphony_home") || return
  if "$stage_only"; then printf '%s\n' "$destination"; return; fi
  next="$package_app_home/.current-$$"
  ln -s "releases/$package_version-$package_revision" "$next" || return
  package_helper replace-link "$next" "$package_app_home/current" || { rm -f "$next"; return 1; }
  mkdir -p "$bin_dir" "$skills_dir" || return
  [ -L "$bin_dir/symphony" ] || ln -s "$package_app_home/current/scripts/symphony" "$bin_dir/symphony" || return
  for skill in "${managed_skills[@]}"; do
    target=$(global_skill_path "$skill")
    [ -L "$target" ] || ln -s "$package_app_home/current/.codex/skills/$skill" "$target" || return
  done
  (umask 077; mkdir -p "$config_home/projects") || return
  printf 'Installed Symphony %s (%s)\nCommand: %s/symphony\nConfiguration: %s\n' "$package_version" "$package_revision" "$bin_dir" "$config_home"
  case ":$PATH:" in *":$bin_dir:"*) ;; *) printf 'Add this directory to PATH: export PATH="%s:$PATH"\n' "$bin_dir" ;; esac
  printf 'Next: inside your project, symphony init --linear-project SLUG, edit the printed credentials file, then symphony auth codex login and symphony run.\n'
)

package_doctor() {
  local json=false ready=true agent=codex
  while [ "$#" -gt 0 ]; do
    case "$1" in --json) json=true; shift ;; --host) die 'packaged installs run in containers; host execution needs a source development install' ;;
      --bin-dir|--config-dir|--skills-dir) [ "$#" -ge 2 ] || die "$1 requires a path"; case "$1" in --bin-dir) bin_dir=$2 ;; --config-dir) config_home=$2 ;; --skills-dir) skills_dir=$2 ;; esac; shift 2 ;;
      *) die "unknown doctor option: $1" ;;
    esac
  done
  (container_runtime) >/dev/null 2>&1 || ready=false
  [ -L "$bin_dir/symphony" ] && [ "$(readlink "$bin_dir/symphony")" = "$package_app_home/current/scripts/symphony" ] || ready=false
  global_skills_ready || ready=false
  package_helper verify-files "$symphony_home" >/dev/null || ready=false
  if "$json"; then
    printf '{"mode":"doctor","installation":"packaged","ready":%s,"version":"%s","source_revision":"%s","architecture":"%s","codex_image":"%s","claude_image":"%s"}\n' "$ready" "$package_version" "$package_revision" "$package_arch" "$(package_image codex)" "$(package_image claude)"
  else
    printf 'Symphony %s (%s), packaged macOS %s\nReady: %s\n' "$package_version" "$package_revision" "$package_arch" "$ready"
    "$ready" || printf 'Check the Docker Desktop/OrbStack engine and installed command/skills. No host build toolchain is required.\n' >&2
  fi
  "$ready"
}

package_update() { die 'this bundle lacks the packaged release updater; use a complete release bundle'; }
package_source_update_lock() { die 'this bundle lacks the packaged release updater'; }

# JAY-688 extends the same manager and lifecycle updater, without a second state owner.
if [ -f "$symphony_home/scripts/symphony-package-update.sh" ]; then . "$symphony_home/scripts/symphony-package-update.sh"; fi
