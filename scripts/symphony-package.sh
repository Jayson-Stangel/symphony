# Sourced only by a release bundle. Lifecycle state continues to belong to the launcher.
packaged_install=true
[ -z "${SYMPHONY_RUNNER_IMAGE:-}" ] || die 'packaged runner images are selected by the release and installed updater; remove SYMPHONY_RUNNER_IMAGE'
case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) package_platform=macos; package_arch=arm64 ;;
  Linux/x86_64) package_platform=linux_x86_64; package_arch=x86_64 ;;
  *) die 'unsupported host architecture or OS; packaged installs support macOS ARM64 and Linux x86-64' ;;
esac
symphony_home=$(cd "$symphony_home" && pwd -P)
IFS= read -r bundle_platform < "$symphony_home/host-platform" || die 'bundle is missing its host platform marker'
[ "$bundle_platform" = "$package_platform" ] || die 'bundle platform does not match this host'
package_helper() { "$symphony_home/libexec/symphony-host" "$@"; }
package_helper validate-manager "$symphony_home/release.json" || exit 1
package_version=$(package_helper get "$symphony_home/release.json" version)
package_revision=$(package_helper manager-revision "$symphony_home/release.json")
package_base_version=$(package_helper manager-base-version "$symphony_home/release.json")
package_storage_revision=$(package_helper get "$symphony_home/release.json" source_revision)
# Stock updaters stage an envelope under its legacy component source. That
# immutable storage key is independent from the real compiled manager source.
package_storage_key="$package_version-$package_storage_revision"
package_channel=$(package_helper get "$symphony_home/release.json" channel)
package_manifest_platform() { package_helper get "$1" package_platform 2>/dev/null || printf 'macos\n'; }
[ "$(package_manifest_platform "$symphony_home/release.json")" = "$package_platform" ] || die 'bundle platform does not match this host'
[ "$(package_helper host-platform)" = "$package_platform" ] || die 'helper platform does not match this host'
# Keep legacy schema1/macOS selection stable so already-installed updaters can
# consume new releases; only Linux selects the additive platform bundle.
package_bundle_field() {
  if [ "$package_platform" = macos ]; then printf 'bundle.%s\n' "$1"
  else printf 'bundles.linux_x86_64.%s\n' "$1"; fi
}
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
  symphony runner doctor [--agent codex|claude] [--sandbox-only]
  symphony runner import --agent codex|claude --archive PATH
  symphony runner start|stop|status|logs|drain|resume|recheck
  symphony update [--agent codex|claude | --rollback-runner]
  symphony environment select --agent codex|claude IMAGE
  symphony environment show [--json]

This release bundle requires Docker Engine (Ubuntu) or Docker Desktop/OrbStack
(macOS). No host build or
agent toolchain is needed. From an existing project checkout, initialize it, edit
the printed private credentials file, authenticate the native container login,
and run. Runner images are pulled by immutable release digest; they never build
locally. Use the installed updater for release changes and runner recovery.
See the packaged installation guide for isolated candidate storage and namespaces.
USAGE
}

package_image() {
  local image
  image=$(package_helper release-get "$symphony_home/release.json" "distribution.${1:-codex}.$package_arch" 2>/dev/null) || image=
  if [ -n "$image" ]; then printf '%s\n' "$image"
  else package_helper release-get "$symphony_home/release.json" "runners.${1:-codex}.$package_arch.image"; fi
}
package_release_image() {
  local runtime=$1 image=$2 agent=$3 manager_revision=$4 platform revision inputs
  [ "$image" = "$(package_image "$agent")" ] || die 'runner image is outside the selected release manifest'
  [ "$manager_revision" = "$package_revision" ] || die 'manager revision differs from selected release'
  revision=$(package_helper release-get "$symphony_home/release.json" "runners.$agent.$package_arch.source_revision")
  platform=$(package_helper release-get "$symphony_home/release.json" "runners.$agent.$package_arch.platform")
  container_engine_ready "$runtime" || return
  if ! "$runtime" image inspect "$image" >/dev/null 2>&1; then
    case "$image" in
      localhost:*/*|127.0.0.1:*/*|localhost/*|127.0.0.1/*)
        die "selected candidate $agent image is absent from the cache. Import its matching companion before login: symphony runner import --agent $agent --archive PATH. Repeat after pruning the image; see docs/candidate-runner-provisioning.md" ;;
    esac
    "$runtime" pull --platform "$platform" "$image" || die "cannot pull released $agent image; engine is reachable. Check registry network access and release availability: $image"
  fi
  [ "$("$runtime" image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")" = "$revision" ] || die 'runner source revision does not match selected release'
  [ "$("$runtime" image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = "$platform" ] || die 'runner architecture does not match selected release'
  [ "$("$runtime" image inspect --format '{{ index .Config.Labels "com.symphony.agent" }}' "$image")" = "$agent" ] || die 'runner provider does not match selected release'
  if [ "$(package_helper release-get "$symphony_home/release.json" schema_version)" = 2 ]; then
    inputs=$(package_helper release-get "$symphony_home/release.json" "runners.$agent.$package_arch.input_identity")
    [ "$("$runtime" image inspect --format '{{ index .Config.Labels "com.symphony.inputs.sha256" }}' "$image")" = "$inputs" ] || die 'runner build inputs differ from selected release'
    [ "$("$runtime" image inspect --format '{{ index .Config.Labels "com.symphony.protocol" }}' "$image")" = 1 ] || die 'runner protocol is incompatible with this manager'
    "$runtime" image inspect "$image" | package_helper validate-runtime-image "$symphony_home/release.json" "$agent" "$package_arch" "$image" || die 'runner config or qualified manifest differs from original component'
  fi
}
# One compatibility admission for explicitly selected and retained protocol-1
# images. Inspect is metadata validation, not runtime requalification or a build.
package_selected_image_compatible() {
  local runtime=$1 image=$2 agent=$3
  "$runtime" image inspect "$image" >/dev/null 2>&1 || die 'selected environment image is missing; obtain the exact image and select it again'
  "$runtime" image inspect "$image" | package_helper validate-selected-image "$agent" "$package_arch" "$image" || die 'selected environment is incompatible; inspect its runtime, provider and layout requirements'
}

# Validate retained project images independently of the selected release image.
# An unlabeled historical image needs exact immutable previous-schema1 evidence.
package_retained_image_compatible() {
  local runtime=$1 image=$2 agent=$3 previous=${4:-} protocol platform legacy revision receipt directory temporary
  [[ "$image" =~ ^sha256:[a-f0-9]{64}$ ]] || die 'invalid retained runner config identity'
  case "$agent" in codex|claude) ;; *) die 'invalid retained runner provider' ;; esac
  [ "$("$runtime" image inspect --format '{{.Id}}' "$image")" = "$image" ] || die 'retained runner config differs'
  [ "$("$runtime" image inspect --format '{{ index .Config.Labels "com.symphony.agent" }}' "$image")" = "$agent" ] || die 'retained runner provider differs'
  platform=linux/arm64; [ "$package_arch" != x86_64 ] || platform=linux/amd64
  [ "$("$runtime" image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" = "$platform" ] || die 'retained runner platform differs'
  protocol=$("$runtime" image inspect --format '{{ index .Config.Labels "com.symphony.protocol" }}' "$image")
  if [ "$protocol" = 1 ]; then package_selected_image_compatible "$runtime" "$image" "$agent"; return; fi
  case "$protocol" in ''|'<no value>') ;; *) die 'retained runner protocol is incompatible with this manager' ;; esac
  directory="$runner_home/compatibility"
  receipt="$directory/${image#sha256:}.schema1"
  if [ -e "$directory" ] || [ -L "$directory" ]; then package_helper owned-directory "$directory" || return; fi
  if [ -e "$receipt" ] || [ -L "$receipt" ]; then
    package_helper owned-file "$receipt" || return
    previous=$(cat "$receipt")
  fi
  [ -n "$previous" ] || die 'retained legacy runner has no verified migration evidence'
  previous=$(package_verified_home "$previous") || return
  [ "$(package_helper get "$previous/release.json" schema_version)" = 1 ] || die 'retained legacy runner requires exact previous schema1 evidence'
  legacy=$(package_helper get "$previous/release.json" "runners.$agent.$package_arch.image")
  revision=$(package_helper get "$previous/release.json" "runners.$agent.$package_arch.source_revision")
  [ "$("$runtime" image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image")" = "$revision" ] || die 'retained legacy runner source differs from verified migration source'
  "$runtime" image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$image" | grep -Fqx -- "$legacy" || die 'retained legacy runner digest differs from verified migration image'
  if [ ! -e "$receipt" ]; then
    if [ ! -e "$directory" ]; then (umask 077; mkdir "$directory") || return; fi
    package_helper owned-directory "$directory" || return
    temporary=$(mktemp "$directory/.migration.XXXXXX") || return
    printf '%s\n' "$previous" > "$temporary"
    # Link with no-overwrite semantics; never replace a conflicting receipt.
    if ! ln "$temporary" "$receipt"; then rm "$temporary"; die 'legacy migration receipt changed concurrently'; fi
    rm "$temporary" || return
    package_helper owned-file "$receipt" || return
  fi
}

package_check_retained_images() {
  local previous=$1 metadata image agent runtime
  runtime=$(container_runtime)
  if declare -F selected_environment_validate_all >/dev/null; then selected_environment_validate_all "$runtime" || return; fi
  for metadata in "$runner_home"/*.env; do
    [ -e "$metadata" ] || [ -L "$metadata" ] || continue
    package_helper owned-file "$metadata" || return
    image=$(sed -n 's/^SYMPHONY_RUNNER_IMAGE_ID=//p' "$metadata")
    agent=$(sed -n 's/^SYMPHONY_RUNNER_AGENT=//p' "$metadata")
    package_retained_image_compatible "$runtime" "$image" "$agent" "$previous" || return
  done
}

package_build_image() {
  local agent=${1:-codex} runtime image
  runtime=$(container_runtime); image=$(runner_image "$agent")
  case "$image" in
    sha256:*)
      # An already-retained project image is pinned by the existing runner metadata.
      "$runtime" image inspect "$image" >/dev/null 2>&1 || die 'retained runner image is missing; recover through symphony update'
      local previous=
      if [ -L "$package_app_home/previous" ] && [ -d "$package_app_home/previous" ]; then previous=$(cd "$package_app_home/previous" && pwd -P) || return; fi
      package_retained_image_compatible "$runtime" "$image" "$agent" "$previous"
      ;;
    *) package_release_image "$runtime" "$image" "$agent" "$package_revision" ;;
  esac
}

# Explicit environment selection wins. A manager transition otherwise retains
# the same-provider project image instead of silently selecting the new default.
package_transition_image() {
  local root=$1 agent=$2 image= metadata
  if declare -F selected_environment_image >/dev/null; then image=$(selected_environment_image "$root" "$agent") || return; fi
  if [ -n "$image" ]; then printf '%s\n' "$image"; return; fi
  metadata=$(runner_env_file "$root") || return
  if [ -e "$metadata" ] || [ -L "$metadata" ]; then
    package_helper owned-file "$metadata" || return
    if [ "$(sed -n 's/^SYMPHONY_RUNNER_AGENT=//p' "$metadata")" = "$agent" ]; then
      image=$(sed -n 's/^SYMPHONY_RUNNER_IMAGE_ID=//p' "$metadata")
      [[ "$image" =~ ^sha256:[a-f0-9]{64}$ ]] || die 'invalid retained runner image identity'
      printf '%s\n' "$image"; return
    fi
  fi
  package_image "$agent"
}

package_transition_image_compatible() {
  local runtime=$1 image=$2 agent=$3 revision=$4 previous=
  case "$image" in
    sha256:*)
      if [ -L "$package_app_home/previous" ] && [ -d "$package_app_home/previous" ]; then previous=$(cd "$package_app_home/previous" && pwd -P) || return; fi
      package_retained_image_compatible "$runtime" "$image" "$agent" "$previous" ;;
    *) package_release_image "$runtime" "$image" "$agent" "$revision" ;;
  esac
}

# A shared manager update must not silently change another project's retained image.
package_use_project_image() {
  local root=$1 agent=$2 metadata image revision
  if declare -F environment_use_project >/dev/null; then environment_use_project "$root" "$agent" || return; fi
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

# Verify the selected home's own helper, not the currently running manager's
# compiled revision: previous and future release homes have distinct sources.
package_verify_home_source() {
  local source=$1 count actual expected
  package_helper owned-file "$source/libexec/symphony-host" || return
  count=$(LC_ALL=C grep -Ecx '[a-f0-9]{64}  \./libexec/symphony-host' "$source/files.sha256" || true)
  [ "$count" = 1 ] || die 'bundle inventory must bind its exact host helper'
  actual=$("$source/libexec/symphony-host" build-revision) || return
  expected=$(package_helper manager-revision "$source/release.json") || return
  [ "$actual" = "$expected" ] || die 'selected home helper differs from actual manager source revision'
}

package_stage_bundle() {
  local source=$1 destination stage version revision
  package_helper validate-release "$source/release.json" || return
  version=$(package_helper get "$source/release.json" version) || return
  revision=$(package_helper get "$source/release.json" source_revision) || return
  [ "$(package_manifest_platform "$source/release.json")" = "$package_platform" ] || die 'bundle platform does not match this host'
  package_helper verify-files "$source" || return
  package_verify_home_source "$source" || return
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

# This file belongs to application storage, not the immutable release. It keeps
# install-time overrides together without editing a user's shell startup files.
package_shell_activation_preflight() {
  local activation="$package_app_home/activate.sh" header
  if [ -e "$activation" ] || [ -L "$activation" ]; then
    package_helper owned-file "$activation" || return
    IFS= read -r header < "$activation" || return
    [ "$header" = '# Symphony shell activation v1' ] || die "refusing to replace existing shell activation file: $activation"
  fi
}

package_shell_environment() {
  local name
  printf '# Symphony shell activation v1\n# Source in bash or zsh; generated by the per-user installer.\n'
  printf 'unset SYMPHONY_HOME\n'
  printf 'export SYMPHONY_APP_HOME=%q\n' "$package_app_home"
  printf 'export SYMPHONY_BIN_DIR=%q\n' "$bin_dir"
  printf 'export SYMPHONY_CONFIG_HOME=%q\n' "$config_home"
  printf 'export SYMPHONY_SKILLS_DIR=%q\n' "$skills_dir"
  printf 'export SYMPHONY_RUNNER_HOME=%q\n' "$runner_home"
  printf 'export SYMPHONY_RUNNER_NAMESPACE=%q\n' "$runner_namespace"
  # Only explicit engine selections are retained; never serialize credentials or
  # the rest of the installing process's environment into a sourced file.
  for name in SYMPHONY_CONTAINER_RUNTIME DOCKER_CONTEXT DOCKER_HOST DOCKER_CONFIG; do
    if [ "${!name+x}" = x ]; then printf 'export %s=%q\n' "$name" "${!name}"
    else printf 'unset %s\n' "$name"; fi
  done
  printf 'export PATH=%q:"$PATH"\nhash -r\n' "$bin_dir"
}

package_shell_activation_write() {
  local temporary
  package_shell_activation_preflight || return
  temporary=$(mktemp "$package_app_home/.activate.XXXXXX") || return
  if ! package_shell_environment > "$temporary"; then rm -f "$temporary"; return 1; fi
  if ! mv -f "$temporary" "$package_app_home/activate.sh"; then rm -f "$temporary"; return 1; fi
}

package_shell_context() {
  local selected runtime context
  selected=$(type -P symphony) || selected=
  printf 'Running launcher: %s/scripts/symphony\nInstalled command: %s/symphony\n' "$symphony_home" "$bin_dir"
  printf 'PATH command (fresh lookup): %s\n' "${selected:-not found}"
  if [ "$selected" != "$bin_dir/symphony" ]; then
    printf 'Command activation required: PATH does not select this installation.\n'
  fi
  printf 'Application: %s\nConfiguration: %s\nSkills: %s\nRunner storage: %s\nNamespace: %s\n' \
    "$package_app_home" "$config_home" "$skills_dir" "$runner_home" "${runner_namespace:-default}"
  runtime=$(container_runtime 2>/dev/null) || runtime=
  if [ -n "${DOCKER_CONTEXT:-}" ]; then context=$DOCKER_CONTEXT
  elif [ -n "${DOCKER_HOST:-}" ]; then context='DOCKER_HOST override (value omitted)'
  elif [ -n "$runtime" ]; then context=$("$runtime" context show 2>/dev/null) || context='unavailable'
  else context=unavailable; fi
  printf 'Container runtime: %s\nDocker context: %s\n' "${runtime:-unavailable}" "$context"
  printf 'The parent shell may also have aliases/functions or cached commands; check with type -a symphony.\n'
}

package_shell_activation_instructions() {
  if [ ! -f "$package_app_home/activate.sh" ]; then
    printf 'This installation has no saved activation file. For this effective context, run in bash/zsh:\n'
    package_shell_environment
    printf 'Explicitly retain this block in your own shell profile if wanted; no profiles were changed.\n'
    return
  fi
  printf 'Activate this installation in the current bash/zsh terminal:\n  source %q\n' "$package_app_home/activate.sh"
  printf 'For fresh terminals, explicitly add that same source line to your own ~/.zshrc (zsh) or ~/.bashrc (interactive bash); a bash login profile must source ~/.bashrc.\n'
  printf 'For a headless shell, source it before invoking Symphony. No shell profiles were changed.\n'
  printf 'Verify selection: type -a symphony; symphony --version\n'
  printf 'A shell alias/function can still override PATH; inspect it before changing it.\n'
}

package_preflight_links() {
  local source target skill
  package_shell_activation_preflight || return
  source="$package_app_home/current/scripts/symphony"; target="$bin_dir/symphony"
  if [ -L "$target" ]; then
    [ "$(readlink "$target")" = "$source" ] || die "refusing to replace existing command link: $target (expected $source). Choose a separate --bin-dir for this installation; preserve the existing command."
  elif [ -e "$target" ]; then die "refusing to replace existing command: $target (expected link to $source). Choose a separate --bin-dir for this installation; preserve the existing command."; fi
  for skill in "${managed_skills[@]}"; do
    source="$package_app_home/current/.codex/skills/$skill"; target=$(global_skill_path "$skill")
    if [ -L "$target" ]; then [ "$(readlink "$target")" = "$source" ] || die "refusing to replace existing global skill link: $target"
    elif [ -e "$target" ]; then die "refusing to replace existing global skill: $target"; fi
  done
  if [ -e "$package_app_home/current" ] || [ -L "$package_app_home/current" ]; then
    [ -L "$package_app_home/current" ] || die 'application current path is not a managed release link'
    [ "$(cd "$package_app_home/current" && pwd -P)" = "$package_app_home/releases/$package_storage_key" ] || die 'another version is installed; use symphony update'
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
  bin_dir=$(package_helper canonical-path "$bin_dir") || return
  config_home=$(package_helper canonical-path "$config_home") || return
  skills_dir=$(package_helper canonical-path "$skills_dir") || return
  resolve_runner_home
  runner_home=$(package_helper canonical-path "$runner_home") || return
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
  # Another installation may share command/skill paths while using a different
  # application lock. Recheck after the long copy before any activation.
  package_preflight_links || return
  package_shell_activation_write || return
  next="$package_app_home/.current-$$"
  package_helper ensure-link "releases/$package_storage_key" "$next" || return
  package_helper replace-link "$next" "$package_app_home/current" || { rm -f "$next"; return 1; }
  mkdir -p "$bin_dir" "$skills_dir" || return
  package_helper ensure-link "$package_app_home/current/scripts/symphony" "$bin_dir/symphony" || return
  for skill in "${managed_skills[@]}"; do
    target=$(global_skill_path "$skill")
    package_helper ensure-link "$package_app_home/current/.codex/skills/$skill" "$target" || return
  done
  (umask 077; mkdir -p "$config_home/projects") || return
  printf 'Installed Symphony %s (%s)\nCommand: %s/symphony\nConfiguration: %s\n' "$package_version" "$package_revision" "$bin_dir" "$config_home"
  package_shell_context
  package_shell_activation_instructions
  if [ "$package_channel" = candidate ]; then printf 'Candidate image prerequisites: symphony runner import --agent codex (or claude) shows the exact companion to download before native login. See the bundled candidate provisioning guide.\n'; fi
  printf 'Next: inside your project, symphony init --linear-project SLUG, edit the printed credentials file, then symphony auth codex login and symphony run.\n'
)

package_doctor() {
  local json=false ready=true agent=codex root= selected=null selection_file= runtime=
  while [ "$#" -gt 0 ]; do
    case "$1" in --json) json=true; shift ;; --host) die 'packaged installs run in containers; host execution needs a source development install' ;;
      --bin-dir|--config-dir|--skills-dir) [ "$#" -ge 2 ] || die "$1 requires a path"; case "$1" in --bin-dir) bin_dir=$2 ;; --config-dir) config_home=$2 ;; --skills-dir) skills_dir=$2 ;; esac; shift 2 ;;
      *) die "unknown doctor option: $1" ;;
    esac
  done
  resolve_runner_home
  (container_runtime) >/dev/null 2>&1 || ready=false
  [ -L "$bin_dir/symphony" ] && [ "$(readlink "$bin_dir/symphony")" = "$package_app_home/current/scripts/symphony" ] || ready=false
  global_skills_ready || ready=false
  package_helper verify-files "$symphony_home" >/dev/null || ready=false
  if declare -F environment_selection_file >/dev/null; then
    root=$(repo_root 2>/dev/null) || root=
    if [ -n "$root" ]; then
      selection_file=$(environment_selection_file "$root") || ready=false
      if [ -e "$selection_file" ] || [ -L "$selection_file" ]; then
        if environment_selection_read "$selection_file"; then
          selected=$(cat "$selection_file")
          runtime=$(container_runtime) || ready=false
          if [ -n "$runtime" ]; then
            selected_environment_validate_record "$runtime" "$selection_file" || ready=false
          fi
        else ready=false; fi
      fi
    fi
  fi
  if "$json"; then
    printf '{"mode":"doctor","installation":"packaged","scope":"installation","provider_project_readiness":"untested","ready":%s,"version":"%s","source_revision":"%s","base_version":"%s","storage_revision":"%s","architecture":"%s","codex_image":"%s","claude_image":"%s","environment":%s}\n' "$ready" "$package_version" "$package_revision" "$package_base_version" "$package_storage_revision" "$package_arch" "$(package_image codex)" "$(package_image claude)" "$selected"
  else
    printf 'Symphony %s (%s), packaged %s %s\nInstallation ready: %s\n' "$package_version" "$package_revision" "$package_platform" "$package_arch" "$ready"
    package_shell_context
    package_shell_activation_instructions
    printf 'Readiness scope: installation files, command/skills and runtime CLI. Engine access, image provisioning, project launch/sandbox, native authentication and Git require symphony runner doctor. Tracker access and completed tasks remain untested.\n'
    if [ "$selected" != null ]; then printf 'Selected environment: %s (runtime %s)\n' "$(package_helper get "$selection_file" image_id)" "$(package_helper get "$selection_file" runtime_revision)"; fi
    "$ready" || printf 'Check the Docker engine and installed command/skills. No host build toolchain is required.\n' >&2
  fi
  "$ready"
}

package_update() { die 'this bundle lacks the packaged release updater; use a complete release bundle'; }
package_source_update_lock() { die 'this bundle lacks the packaged release updater'; }

# JAY-688 extends the same manager and lifecycle updater, without a second state owner.
if [ -f "$symphony_home/scripts/symphony-package-update.sh" ]; then . "$symphony_home/scripts/symphony-package-update.sh"; fi
