# Packaged updates share the existing runner transition journal and ownership.
# This file is sourced by the packaged launcher after its portable helpers.

package_current_home() {
  local link
  [ -L "$package_app_home/current" ] || die 'packaged current link is missing; rerun the verified installer'
  link=$(readlink "$package_app_home/current")
  case "$link" in /*) ;; *) link="$package_app_home/$link" ;; esac
  (cd "$link" && pwd -P) || die 'packaged current release is unavailable'
}

package_update_lock() {
  local mode=${1:-acquire} path
  package_app_home=$(cd "$package_app_home" && pwd -P) || die 'packaged application storage is unavailable'
  package_helper owned-directory "$package_app_home" || return
  path="$package_app_home/update.lock"
  if [ "$mode" = acquire ]; then
    [ ! -L "$path" ] && { [ ! -e "$path" ] || [ -f "$path" ]; } || die 'unsafe packaged updater lock'
    exec 8>>"$path" || return
  fi
  package_helper lock "$path" 8 "$mode"
}

# This is only a locator. All selected release and recovery information lives
# in the existing project transition directory, including with config overrides.
package_pending_transition() {
  local pointer="$package_app_home/pending-transition" journal
  if [ ! -L "$pointer" ]; then
    [ ! -e "$pointer" ] || die 'unknown packaged transition locator; updater refused'
    return
  fi
  journal=$(readlink "$pointer")
  case "$journal" in /*.transition) ;; *) die 'unsafe packaged transition locator' ;; esac
  if [ -d "$journal" ]; then
    package_helper owned-directory "$journal" || return
    [ -f "$journal/package.target" ] && [ -f "$journal/package.previous" ] || die 'pending packaged transition identity is incomplete'
    package_verified_home "$(cat "$journal/package.target")" >/dev/null || return
    package_verified_home "$(cat "$journal/package.previous")" >/dev/null || return
    printf '%s\n' "$journal"
  elif [ -e "$journal" ] || [ -L "$journal" ]; then
    die 'pending packaged transition path is unsafe'
  else
    # Publication has not happened, or completion already renamed the journal.
    # Shared updater ownership proves no writer can still publish concurrently.
    if [ -e "$journal.complete" ] || [ -L "$journal.complete" ]; then
      package_helper owned-directory "$journal.complete" || return
      printf '%s\n' "$journal.complete"
      return
    fi
    rm -f "$pointer"
  fi
}

package_recover_completed_transition() {
  local complete=$1 selected
  package_helper owned-directory "$complete" || return
  if [ -f "$complete/package.committed" ]; then
    selected=$(package_verified_home "$(cat "$complete/package.committed")") || return
    [ "$(package_current_home)" = "$selected" ] || die 'completed transition manager differs; recovery refused'
  elif [ -f "$complete/package.target" ]; then
    die 'completed packaged transition has no verified committed manager; recovery refused'
  fi
  package_remove_transition_fields "$complete" || return
  rm -f "$complete/target" "$complete/port" "$complete/previous.env" "$complete/image" \
    "$complete/image.next" "$complete/previous.env.next" || return
  rmdir "$complete" || die 'unknown completed packaged transition metadata; recovery refused'
  printf 'Previously completed packaged transition recovered; no newer release selected.\n'
}

package_register_transition() {
  local journal=$1 pending
  pending=$(package_pending_transition) || return
  [ -z "$pending" ] || [ "$pending" = "$journal" ] || die 'another project has an unfinished packaged update; finish or roll it back first'
  if [ -z "$pending" ]; then package_atomic_link "$journal" "$package_app_home/pending-transition"; fi
}

package_verified_home() {
  local target=$1 canonical
  [ -d "$target" ] && [ ! -L "$target" ] || die 'retained packaged release is missing or unsafe'
  canonical=$(cd "$target" && pwd -P)
  case "$canonical" in "$package_app_home"/releases/*) ;; *) die 'release is outside this packaged installation' ;; esac
  [ "$(dirname "$canonical")" = "$package_app_home/releases" ] || die 'invalid versioned release directory'
  package_helper validate-manifest "$canonical/release.json" || return
  package_helper verify-files "$canonical" || return
  printf '%s\n' "$canonical"
}

package_atomic_link() {
  local target=$1 destination=$2 temporary
  [ ! -e "$destination" ] || [ -L "$destination" ] || die "refusing conflicting packaged path: $destination"
  temporary=$(mktemp "$package_app_home/.activate.XXXXXX") || return
  rm "$temporary" || return
  ln -s "$target" "$temporary" || return
  # BSD and GNU mv follow a destination symlink to a directory unless the
  # destination is removed or rename(2) is used. The native helper owns rename.
  package_helper replace-link "$temporary" "$destination"
}

package_activate_home() {
  local target=$1 previous=${2:-} current
  target=$(package_verified_home "$target") || return
  current=$(package_current_home) || return
  if [ "$current" = "$target" ]; then return; fi
  if [ -n "$previous" ]; then
    [ "$current" = "$previous" ] || die 'current packaged manager changed; updater refused activation'
  fi
  package_atomic_link "$current" "$package_app_home/previous" || return
  package_atomic_link "$target" "$package_app_home/current"
}

# Called before the existing transition directory is atomically published.
package_write_transition() {
  [ -n "${package_update_target:-}" ] || return 0
  printf '%s\n' "$package_update_target" >"$1/package.target" || return
  printf '%s\n' "$package_update_previous" >"$1/package.previous" || return
  printf '%s\n' "$(source_revision)" >"$1/package.revision" || return
  package_register_transition "$package_update_journal"
}

# Called at the existing transition's final commit point. Until activation has
# succeeded the journal remains available to both retry and retained rollback.
package_finish_transition() {
  local journal=$1 target previous current selected
  [ -f "$journal/package.target" ] || return 0
  previous=$(cat "$journal/package.previous")
  selected=$(cat "$journal/package.target")
  if [ "${package_update_rollback:-false}" = true ]; then target=$previous
  else target=$selected; fi
  target=$(package_verified_home "$target") || return
  current=$(package_current_home) || return
  [ "$current" = "$previous" ] || [ "$current" = "$selected" ] || die 'current manager differs from the retained transition; activation refused'
  if [ "$current" != "$target" ]; then
    package_activate_home "$target" "$current" || return
  fi
  printf '%s\n' "$target" >"$journal/package.committed.next" || return
  mv "$journal/package.committed.next" "$journal/package.committed"
}

package_remove_transition_fields() {
  local pointer="$package_app_home/pending-transition" original=${1%.complete}
  rm -f "$1/package.target" "$1/package.previous" "$1/package.revision" \
    "$1/package.committed" "$1/package.committed.next" || return
  if [ -L "$pointer" ] && [ "$(readlink "$pointer")" = "$original" ]; then rm "$pointer"; fi
}

package_download() {
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    --connect-timeout 15 --max-time 300 --output "$2" "$1" || die "release download failed: $1; installed manager and runner retained"
}

package_extract_bundle() {
  local archive=$1 destination=$2 entry listing
  listing=$(tar -tzf "$archive") || die 'release archive cannot be read'
  [ -n "$listing" ] || die 'release archive is empty'
  while IFS= read -r entry; do
    case "$entry" in /*|..|../*|*/../*|*/..|*\\*) die 'release archive contains an unsafe path' ;; esac
    if printf '%s' "$entry" | LC_ALL=C grep -q '[^ -~]'; then die 'release archive contains an unsupported path'; fi
  done <<EOF
$listing
EOF
  # Bundles contain only ordinary files and directories. Reject links, devices,
  # and other special entries before extracting even a checksum-verified asset.
  tar -tvzf "$archive" | LC_ALL=C awk 'substr($0,1,1) != "-" && substr($0,1,1) != "d" {bad=1} END {exit bad}' || die 'release archive contains links or special files'
  mkdir "$destination" || die 'release extraction directory cannot be created'
  tar -xzf "$archive" -C "$destination" || die 'release archive extraction failed'
}

package_stage_download() (
  set -e
  local requested=$1 candidate_manifest=$2 candidate_bundle=$3 temporary manifest bundle tag version revision filename checksum actual target agent arch field
  temporary=$(mktemp -d "$package_app_home/.download.XXXXXX") || return
  package_download_temporary=$temporary
  trap 'rm -rf -- "$package_download_temporary"' EXIT
  manifest="$temporary/release.json"
  if [ -n "$candidate_manifest" ]; then
    [ -n "$runner_namespace" ] || die 'candidate package updates require an isolated SYMPHONY_RUNNER_NAMESPACE'
    [ -r "$candidate_manifest" ] && [ -r "$candidate_bundle" ] || die 'candidate manifest and bundle must be readable files'
    cp "$candidate_manifest" "$manifest" || return
  else
    if [ -n "$requested" ]; then
      case "$requested" in *[!0-9A-Za-z.+-]*|'') die 'invalid release version' ;; esac
      tag="v${requested#v}"
      package_download "https://api.github.com/repos/Jayson-Stangel/symphony/releases/tags/$tag" "$temporary/selection.json"
    else
      package_download 'https://api.github.com/repos/Jayson-Stangel/symphony/releases/latest' "$temporary/selection.json"
    fi
    [ "$(package_helper get "$temporary/selection.json" draft)" = false ] &&
      [ "$(package_helper get "$temporary/selection.json" prerelease)" = false ] || die 'only completed production releases can be installed'
    tag=$(package_helper get "$temporary/selection.json" tag_name)
    case "$tag" in *[!0-9A-Za-z.+-]*|'') die 'invalid production release tag' ;; v[0-9]*) ;; *) die 'invalid production release tag' ;; esac
    package_download "https://github.com/Jayson-Stangel/symphony/releases/download/$tag/release.json" "$manifest"
  fi
  package_helper validate-manifest "$manifest" || return
  version=$(package_helper get "$manifest" version) || return
  revision=$(package_helper get "$manifest" source_revision) || return
  filename=$(package_helper get "$manifest" "$(package_bundle_field filename)") || return
  checksum=$(package_helper get "$manifest" "$(package_bundle_field sha256)") || return
  case "$version" in ''|*[!0-9A-Za-z.+-]*) die 'invalid packaged release version' ;; esac
  [ "$filename" = "symphony-$version-$package_platform.tar.gz" ] || die 'release bundle filename does not match version'
  case "$checksum" in *[!a-f0-9]*|'') die 'invalid release bundle checksum' ;; esac
  [ "${#checksum}" = 64 ] || die 'invalid release bundle checksum'
  if [ -z "$candidate_manifest" ]; then
    [ "$(package_helper get "$manifest" channel)" = stable ] && [ "$tag" = "v$version" ] || die 'release manifest is not the selected production release'
  fi
  bundle="$temporary/$filename"
  if [ -n "$candidate_manifest" ]; then cp "$candidate_bundle" "$bundle" || return
  else package_download "https://github.com/Jayson-Stangel/symphony/releases/download/$tag/$filename" "$bundle"; fi
  actual=$(package_helper sha256 "$bundle") || return
  [ "$actual" = "$checksum" ] || die 'release bundle checksum mismatch; installed manager and runner retained'
  package_extract_bundle "$bundle" "$temporary/unpacked" || return
  [ "$(package_helper get "$temporary/unpacked/release.json" version)" = "$version" ] &&
    [ "$(package_helper get "$temporary/unpacked/release.json" source_revision)" = "$revision" ] || die 'bundle and release manifest identities differ'
  package_helper validate-manifest "$temporary/unpacked/release.json" || return
  [ "$(package_manifest_platform "$temporary/unpacked/release.json")" = "$package_platform" ] || die 'downloaded bundle platform does not match selected host'
  [ "$(package_helper get "$temporary/unpacked/release.json" "$(package_bundle_field filename)")" = "$filename" ] || die 'downloaded bundle platform identity differs'
  package_helper verify-files "$temporary/unpacked" || return
  [ "$(package_helper get "$manifest" channel)" = "$(package_helper get "$temporary/unpacked/release.json" channel)" ] || die 'bundle and release channels differ'
  [ "$(package_helper get "$manifest" schema_version)" = "$(package_helper get "$temporary/unpacked/release.json" schema_version)" ] || die 'bundle and release schemas differ'
  [ "$(package_helper get "$manifest" runners)" = "$(package_helper get "$temporary/unpacked/release.json" runners)" ] || die 'bundle runner identity or qualification differs from release manifest'
  [ "$(package_helper get "$manifest" distribution 2>/dev/null || printf '{}')" = "$(package_helper get "$temporary/unpacked/release.json" distribution 2>/dev/null || printf '{}')" ] || die 'bundle distribution differs from release manifest'
  if [ "$(package_helper get "$manifest" schema_version)" = 2 ]; then
    [ "$(package_helper get "$manifest" compatibility)" = "$(package_helper get "$temporary/unpacked/release.json" compatibility)" ] || die 'bundle compatibility differs from release manifest'
  fi
  # The installer preflights/copies an immutable release without changing PATH,
  # skills, current, project files, credentials, or running containers.
  target=$(package_stage_bundle "$temporary/unpacked") || return
  package_verified_home "$target"
)

package_update() {
  local requested= candidate_manifest= candidate_bundle= transition= rollback_release=false target= previous root= journal= pending
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --release|--candidate-manifest|--candidate-bundle)
        [ "$#" -ge 2 ] || die "$1 requires a value"
        case "$1" in --release) requested=$2 ;; --candidate-manifest) candidate_manifest=$2 ;; --candidate-bundle) candidate_bundle=$2 ;; esac
        shift 2 ;;
      --agent)
        [ "$#" -ge 2 ] || die '--agent requires codex or claude'
        [ -z "$transition" ] || die 'choose only one runner transition'
        validate_agent "$2"; transition=$2; shift 2 ;;
      --rollback-runner|--pause-runner|--migrate-project-identity)
        [ -z "$transition" ] || die 'choose only one runner transition'
        transition=${1#--}; shift ;;
      --rollback-release) rollback_release=true; shift ;;
      --bin-dir|--config-dir|--skills-dir)
        [ "$#" -ge 2 ] || die "$1 requires a path"
        case "$1" in --bin-dir) bin_dir=$2 ;; --config-dir) config_home=$2 ;; --skills-dir) skills_dir=$2 ;; esac
        shift 2 ;;
      --help|-h)
        printf 'Usage: symphony update [--release VERSION | --rollback-release] [--agent codex|claude | --rollback-runner | --pause-runner | --migrate-project-identity]\n'
        printf 'Isolated candidate validation: --candidate-manifest FILE --candidate-bundle FILE\n'; return ;;
      *) die "unknown packaged update option: $1" ;;
    esac
  done
  resolve_runner_home
  if { [ -z "$candidate_manifest" ] && [ -n "$candidate_bundle" ]; } ||
     { [ -n "$candidate_manifest" ] && [ -z "$candidate_bundle" ]; }; then
    die 'candidate manifest and bundle must be supplied together'
  fi
  if [ -n "$requested" ] && { [ -n "$candidate_manifest" ] || "$rollback_release"; }; then die 'choose one packaged release selection'; fi
  if "$rollback_release" && [ -n "$candidate_manifest" ]; then die 'choose one packaged release selection'; fi
  package_update_lock || return
  pending=$(package_pending_transition) || return
  previous=$(package_current_home) || return
  [ "$previous" = "$(cd "$symphony_home" && pwd -P)" ] || die 'invoke the installed current manager before updating'
  if [ -n "$transition" ]; then
    root=$(repo_root); journal="$runner_home/$(project_id "$root").transition"
    if [ "$pending" = "$journal.complete" ]; then
      [ -z "$requested$candidate_manifest" ] && ! "$rollback_release" || die 'complete the retained packaged update before selecting another release'
      package_recover_completed_transition "$pending"
      return
    fi
    [ -z "$pending" ] || [ "$pending" = "$journal" ] || die 'another project has an unfinished packaged update; finish or roll it back first'
    case "$transition" in
      pause-runner|migrate-project-identity|rollback-runner)
        [ -z "$requested$candidate_manifest" ] && ! "$rollback_release" || die 'release selection cannot be combined with this runner control'
        case "$transition" in
          pause-runner) runner_pause "$root" true; return ;;
          migrate-project-identity) [ -z "$pending" ] || die 'finish or roll back the packaged update before migrating project identity'; runner_migrate_identity "$root"; return ;;
          rollback-runner) package_update_rollback=true; runner_rollback "$root"; return ;;
        esac ;;
    esac
    if [ -d "$journal" ]; then
      [ -f "$journal/package.target" ] || die 'pending transition has no packaged release identity; use --rollback-runner'
      [ -z "$requested$candidate_manifest" ] && ! "$rollback_release" || die 'a packaged transition is pending; retry without changing its selected release or use --rollback-runner'
      target=$(package_verified_home "$(cat "$journal/package.target")") || return
      previous=$(cat "$journal/package.previous")
    fi
  elif [ -n "$pending" ]; then
    die 'a project has an unfinished packaged update; retry its --agent update or use --rollback-runner first'
  fi
  if [ -z "$target" ]; then
    if "$rollback_release"; then
      [ -L "$package_app_home/previous" ] || die 'no previous packaged release is retained'
      target=$(readlink "$package_app_home/previous")
      case "$target" in /*) ;; *) target="$package_app_home/$target" ;; esac
      target=$(package_verified_home "$target") || return
    else target=$(package_stage_download "$requested" "$candidate_manifest" "$candidate_bundle") || return; fi
  fi
  # Re-enter the exact verified manager without resolving latest a second time.
  unset SYMPHONY_RUNNER_IMAGE
  exec env SYMPHONY_HOME="$target" SYMPHONY_APP_HOME="$package_app_home" \
    SYMPHONY_BIN_DIR="$bin_dir" SYMPHONY_CONFIG_HOME="$config_home" \
    SYMPHONY_SKILLS_DIR="$skills_dir" SYMPHONY_RUNNER_HOME="$runner_home" \
    "$target/scripts/symphony" _complete-package-update "$previous" "$transition"
}

complete_package_update() {
  [ "$#" -eq 2 ] || die 'invalid packaged update continuation'
  package_update_lock inherited || return
  package_update_target=$(package_verified_home "$symphony_home") || return
  package_update_previous=$(package_verified_home "$1") || return
  # Refuse an incompatible retained project before activation or transition can
  # mutate any installed manager or project. The previous verified schema1
  # manifest is the sole migration route for images without a protocol label.
  package_check_retained_images "$package_update_previous" || return
  if [ -n "$2" ]; then
    local root
    root=$(repo_root)
    package_update_journal="$runner_home/$(project_id "$root").transition"
    # Keep the existing lifecycle function outside a conditional context: it
    # deliberately relies on errexit for synchronous Docker side effects.
    runner_transition "$root" "$2"
  else package_activate_home "$package_update_target" "$package_update_previous" || return; fi
  printf 'Updated packaged manager: %s -> %s\n' "$(basename "$package_update_previous")" "$(basename "$package_update_target")"
}
