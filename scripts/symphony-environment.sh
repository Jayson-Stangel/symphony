# Team-owned image selection for packaged managers. Selection never pulls or builds.
# Lifecycle and updater ownership remain in the launcher.
environment_selection_file() { printf '%s/environments/%s.json\n' "$runner_home" "$(project_id "$1")"; }

environment_selection_read() {
  local file=$1
  package_helper selection-storage-directory "$runner_home" || return
  package_helper owned-directory "$runner_home/environments" || return
  package_helper validate-environment-selection "$file"
}

selected_environment_image() {
  local root=$1 agent=$2 file provider
  "$packaged_install" || return 0
  file=$(environment_selection_file "$root") || return
  if [ ! -e "$file" ] && [ ! -L "$file" ]; then return 0; fi
  environment_selection_read "$file" || return
  provider=$(package_helper get "$file" provider) || return
  [ "$provider" = "$agent" ] || die "selected team environment uses $provider; request that provider or select a compatible $agent environment"
  selected_environment_validate_record "$(container_runtime)" "$file" || return
  package_helper get "$file" image_id
}

environment_use_project() {
  local image
  image=$(selected_environment_image "$1" "$2") || return
  if [ -n "$image" ]; then export SYMPHONY_RUNNER_IMAGE=$image; fi
}

selected_environment_validate_record() {
  local runtime=$1 file=$2 image provider revision platform
  environment_selection_read "$file" || return
  image=$(package_helper get "$file" image_id) || return
  provider=$(package_helper get "$file" provider) || return
  package_selected_image_compatible "$runtime" "$image" "$provider" || return
  revision=$("$runtime" image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image") || return
  platform=$("$runtime" image inspect --format '{{.Os}}/{{.Architecture}}' "$image") || return
  [ "$revision" = "$(package_helper get "$file" runtime_revision)" ] || die 'selected environment runtime revision differs from its immutable image; select the verified image again'
  [ "$platform" = "$(package_helper get "$file" platform)" ] || die 'selected environment platform differs from its immutable image; select the verified image again'
}

selected_environment_validate_all() {
  local runtime=$1 file
  [ ! -e "$runner_home/environments" ] && [ ! -L "$runner_home/environments" ] && return 0
  package_helper selection-storage-directory "$runner_home" || return
  package_helper owned-directory "$runner_home/environments" || return
  for file in "$runner_home/environments"/*.json; do
    [ -e "$file" ] || [ -L "$file" ] || continue
    selected_environment_validate_record "$runtime" "$file" || return
  done
}

environment_select() {
  local root=$1 agent=$2 reference=$3 runtime image revision platform file directory temporary
  validate_agent "$agent"
  [ "${#reference}" -le 512 ] && [[ "$reference" =~ ^[a-zA-Z0-9][a-zA-Z0-9._/@:+-]*$ ]] || die 'invalid image reference; use a local image tag or immutable digest'
  package_update_lock || return
  [ "$(package_current_home)" = "$symphony_home" ] || die 'manager changed; retry selection through the installed command'
  [ -z "$(package_pending_transition)" ] || die 'finish or roll back the pending installed update before selecting an environment'
  # Create private derived storage before the legacy lock path mkdir-p can use
  # the caller's ordinary umask. Existing paths are verified, never chmodded.
  if [ ! -e "$runner_home" ] && [ ! -L "$runner_home" ]; then (umask 077; mkdir -p "$runner_home") || return; fi
  package_helper selection-storage-directory "$runner_home" || return
  runner_project_lock "$root"
  runtime=$(container_runtime)
  "$runtime" image inspect "$reference" >/dev/null 2>&1 || die "team environment is unavailable locally: $reference; build or obtain it with your team tools, then select it again"
  image=$("$runtime" image inspect --format '{{.Id}}' "$reference") || return
  package_selected_image_compatible "$runtime" "$image" "$agent" || return
  revision=$("$runtime" image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image") || return
  [[ "$revision" =~ ^[a-f0-9]{40}$ ]] || die 'team environment must preserve its embedded Symphony runtime source revision'
  platform=$("$runtime" image inspect --format '{{.Os}}/{{.Architecture}}' "$image") || return
  # Probe actual files and executables once at explicit selection. No credentials,
  # host paths, network, listener, or model request are available to this probe.
  "$runtime" run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
    --tmpfs /tmp:rw,nosuid,nodev,size=32m --entrypoint sh "$image" -ceu '
      test -x /opt/symphony/elixir/bin/symphony
      test -r /opt/symphony/skills/commit/SKILL.md
      command -v git >/dev/null
      if [ "$1" = codex ]; then
        test -x /usr/local/bin/symphony-runner
        codex --version >/dev/null
      else
        test -x /usr/local/bin/symphony-claude-runner
        test -r /opt/symphony/claude/auth.mjs
        test -x /usr/bin/tini
        /usr/local/bin/node --version >/dev/null
        cd /opt/symphony/claude
        /usr/local/bin/node --input-type=module -e "await import(\"@anthropic-ai/claude-agent-sdk\"); const r = await import(\"./runtime.mjs\"); const fs = await import(\"node:fs\"); fs.accessSync(r.nativeExecutable(), fs.constants.X_OK)"
      fi
    ' environment-probe "$agent" || die 'team environment lacks required Symphony runtime/provider files; extend the maintained template without replacing its runtime layout'
  directory="$runner_home/environments"
  package_helper selection-storage-directory "$runner_home" || return
  if [ ! -e "$directory" ] && [ ! -L "$directory" ]; then (umask 077; mkdir "$directory") || return; fi
  package_helper owned-directory "$directory" || return
  file=$(environment_selection_file "$root") || return
  if [ -e "$file" ] || [ -L "$file" ]; then environment_selection_read "$file" || return; fi
  temporary=$(mktemp "$directory/.selection.XXXXXX") || return
  printf '{"schema_version":1,"provider":"%s","image_id":"%s","reference":"%s","runtime_revision":"%s","platform":"%s","protocol":1}\n' \
    "$agent" "$image" "$reference" "$revision" "$platform" > "$temporary"
  package_helper validate-environment-selection "$temporary" || { rm "$temporary"; return 1; }
  mv "$temporary" "$file" || { rm "$temporary"; return 1; }
  printf 'Selected team environment: %s\nProvider: %s\nImage: %s\nEmbedded runtime: %s\nUse symphony update --agent %s to transition an existing runner.\n' "$reference" "$agent" "$image" "$revision" "$agent"
}

environment_show() {
  local root=$1 json=$2 file provider image runtime
  file=$(environment_selection_file "$root") || return
  if [ ! -e "$file" ] && [ ! -L "$file" ]; then
    if "$json"; then printf '{"selection":"release"}\n'; else printf 'Environment: release default (or retained project image)\n'; fi
    return
  fi
  environment_selection_read "$file" || return
  provider=$(package_helper get "$file" provider); image=$(package_helper get "$file" image_id)
  runtime=$(container_runtime)
  selected_environment_validate_record "$runtime" "$file" || return
  if "$json"; then cat "$file"
  else
    printf 'Environment: team-owned\nReference: %s\nProvider: %s\nImage: %s\nEmbedded runtime: %s\nManager: %s\n' \
      "$(package_helper get "$file" reference)" "$provider" "$image" "$(package_helper get "$file" runtime_revision)" "$package_revision"
  fi
}

environment() {
  "$packaged_install" || die 'durable team environment selection requires an installed Symphony package'
  local root command=${1:-} agent=codex
  root=$(repo_root)
  shift || true
  case "$command" in
    select)
      if [ "${1:-}" = --agent ]; then [ "$#" -ge 2 ] || die '--agent requires codex or claude'; agent=$2; shift 2; fi
      [ "$#" = 1 ] || die 'Usage: symphony environment select [--agent codex|claude] IMAGE'
      environment_select "$root" "$agent" "$1" ;;
    show)
      case "$*" in '') environment_show "$root" false ;; --json) environment_show "$root" true ;; *) die 'Usage: symphony environment show [--json]' ;; esac ;;
    *) die 'Usage: symphony environment select [--agent codex|claude] IMAGE | show [--json]' ;;
  esac
}
