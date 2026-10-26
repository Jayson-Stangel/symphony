# Supported, explicitly authorized host preparation. Never sourced by sudo
# automatically; no policy removal, Docker restart or package installation.
apparmor_fail() { printf 'AppArmor [%s]: %s\n' "$1" "$2" >&2; return 1; }

apparmor_daemon() {
  local runtime=$1
  if [ -n "${DOCKER_CONTEXT:-}" ]; then
    aa_endpoint=$("$runtime" context inspect "$DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}' 2>/dev/null) || return 1
  elif [ -n "${DOCKER_HOST:-}" ]; then aa_endpoint=$DOCKER_HOST
  else aa_endpoint=$("$runtime" context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null) || return 1; fi
  aa_daemon=$("$runtime" info --format '{{.ID}}' 2>/dev/null) || return 1
  aa_host=$("$runtime" info --format '{{.Name}}' 2>/dev/null) || return 1
  [[ "$aa_daemon" =~ ^[a-zA-Z0-9:-]+$ ]] && [[ "$aa_host" =~ ^[a-zA-Z0-9._-]+$ ]] || return 1
  # Do not print arbitrary endpoints: they can contain credentials.
  printf 'AppArmor daemon: %s (ID %s)\n' "$aa_host" "$aa_daemon"
}

apparmor_local_host() {
  [ "$(uname -s)" = Linux ] && [ "$aa_endpoint" = unix:///var/run/docker.sock ] && [ "$aa_host" = "$(hostname)" ] || {
    apparmor_fail daemon-host 'Preparation requires the selected rootful Linux daemon host and its /var/run/docker.sock. Transfer the verified source-free Linux package to the reported daemon host; run runner apparmor there with its local Docker context. Do not prepare this controller as a substitute.'; return 1;
  }
}

# Root ownership and no group/other writes, including each ancestor. Reject links
# even when they happen to point at an otherwise matching file.
apparmor_safe_path() {
  local path=$1 metadata mode
  while [ "$path" != / ]; do
    [ ! -L "$path" ] && [ -e "$path" ] || return 1
    metadata=$(stat -c '%u %a' "$path") || return 1
    [ "${metadata%% *}" = 0 ] || return 1
    mode=${metadata#* }
    (( (8#$mode & 8#022) == 0 )) || return 1
    path=${path%/*}; [ -n "$path" ] || path=/
  done
}

apparmor_prerequisites() {
  local item lsm
  for item in apparmor_parser systemctl sha256sum stat flock install cmp; do
    command -v "$item" >/dev/null 2>&1 || { apparmor_fail tools "Missing $item; ask the daemon host administrator to install the distribution prerequisite, then retry."; return 1; }
  done
  [ "$(cat /sys/module/apparmor/parameters/enabled 2>/dev/null)" = Y ] || { apparmor_fail kernel 'AppArmor is unavailable in the selected host kernel.'; return 1; }
  lsm=$(cat /sys/kernel/security/lsm 2>/dev/null) || { apparmor_fail kernel 'Cannot read securityfs LSM inventory; run the explicit administrator preparation on the daemon host.'; return 1; }
  case ",$lsm," in *,apparmor,*) ;; *) apparmor_fail kernel 'AppArmor is absent from the active LSM inventory.'; return 1 ;; esac
  [ -e /proc/thread-self/attr/apparmor/exec ] || { apparmor_fail kernel 'The per-thread AppArmor exec interface is unavailable.'; return 1; }
  for item in /etc/apparmor.d /etc/apparmor.d/abi/3.0 /etc/apparmor.d/tunables/global /etc/apparmor.d/abstractions/base /etc/systemd/system; do
    apparmor_safe_path "$item" || { apparmor_fail imports "Missing or unsafe host prerequisite: $item"; return 1; }
  done
  [ "$(systemctl show docker.service --property=LoadState --value 2>/dev/null)" = loaded ] &&
    [ "$(systemctl show apparmor.service --property=LoadState --value 2>/dev/null)" = loaded ] || {
    apparmor_fail tools 'Supported preparation requires systemd docker.service and apparmor.service on the daemon host.'; return 1;
  }
}

apparmor_paths() {
  aa_policy="$symphony_home/scripts/codex-apparmor.profile"
  aa_destination="/etc/apparmor.d/$aa_profile"
  aa_receipt="/etc/apparmor.d/.${aa_profile}.symphony"
  aa_dropdir=/etc/systemd/system/docker.service.d
  aa_dropin="$aa_dropdir/50-$aa_profile.conf"
  aa_parser=$(command -v apparmor_parser)
}

apparmor_verify_boot_guard() {
  local value record prefix metadata matches=0
  local status_pattern='^start_time=\[[^][;{}]*\] ; stop_time=\[[^][;{}]*\] ; pid=[0-9]+ ; code=[A-Za-z_()]+ ; status=-?[0-9]+(/[A-Za-z0-9_+-]+)? \}$'
  value=$(systemctl show docker.service --property=Requires --value) || return 1
  case " $value " in *' apparmor.service '*) ;; *) return 1 ;; esac
  value=$(systemctl show docker.service --property=After --value) || return 1
  case " $value " in *' apparmor.service '*) ;; *) return 1 ;; esac
  value=$(systemctl show docker.service --property=ExecStartPre --value) || return 1
  # systemctl show prints one normalized execution record per line. Match the
  # actual executable, complete argv and failure handling in that same record;
  # an echoed command, destination suffix or ignored failure is not a guard.
  # Keep unrelated daemon pre-commands, such as Docker's modprobe invocation.
  prefix="{ path=$aa_parser ; argv[]=$aa_parser --replace --skip-cache $aa_destination ; ignore_errors=no ; "
  while IFS= read -r record; do
    case "$record" in
      "$prefix"*)
        metadata=${record#"$prefix"}
        # Reject extra fields/delimiters smuggled into a displayed argv value.
        [[ "$metadata" =~ $status_pattern ]] || return 1
        matches=$((matches + 1)) ;;
    esac
  done <<< "$value"
  [ "$matches" = 1 ]
}

apparmor_receipt() { printf 'symphony-apparmor-v1\n%s\n%s\n' "$aa_profile" "$(codex_apparmor_id)"; }
apparmor_boot_guard() {
  printf '[Unit]\nRequires=apparmor.service\nAfter=apparmor.service\n[Service]\nExecStartPre=%s --replace --skip-cache %s\n' "$aa_parser" "$aa_destination"
}

apparmor_conflicts() {
  local path
  for path in "$aa_destination" "$aa_receipt" "$aa_dropin"; do
    if [ -e "$path" ] || [ -L "$path" ]; then
      [ -f "$path" ] && apparmor_safe_path "$path" || { apparmor_fail conflict "Unsafe owned destination: $path"; return 1; }
    fi
  done
  if [ -e "$aa_dropdir" ] || [ -L "$aa_dropdir" ]; then
    [ -d "$aa_dropdir" ] && apparmor_safe_path "$aa_dropdir" || { apparmor_fail conflict 'Unsafe Docker unit drop-in directory.'; return 1; }
  fi
  for path in "/etc/apparmor.d/disable/$aa_profile" "/etc/apparmor.d/force-complain/$aa_profile"; do
    [ ! -e "$path" ] && [ ! -L "$path" ] || { apparmor_fail conflict "Host override conflicts with enforcement: $path"; return 1; }
  done
  if [ -e "$aa_receipt" ]; then
    cmp -s "$aa_receipt" <(apparmor_receipt) && [ -f "$aa_destination" ] &&
      cmp -s "$aa_destination" "$aa_policy" || { apparmor_fail conflict 'Owned policy/receipt differs from the shipped policy; no replacement authorized.'; return 1; }
  else
    [ ! -e "$aa_destination" ] && [ ! -e "$aa_dropin" ] &&
      ! printf '%s\n' "$aa_loaded" | grep -F "$aa_profile (" >/dev/null || {
      apparmor_fail conflict 'Policy/name already exists without a matching owned receipt; administrator must investigate its provenance. Nothing was replaced.'; return 1;
    }
  fi
  if [ -e "$aa_dropin" ]; then
    cmp -s "$aa_dropin" <(apparmor_boot_guard) || { apparmor_fail conflict 'Existing boot guard differs; nothing was replaced.'; return 1; }
  fi
}

runner_apparmor() (
  local apply=false expected= runtime aa_profile aa_endpoint aa_daemon aa_host aa_policy aa_destination aa_receipt aa_dropdir aa_dropin aa_parser aa_loaded stage
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --apply) apply=true; shift ;;
      --daemon-id) [ "$#" -ge 2 ] || { apparmor_fail usage '--daemon-id requires a value'; return 1; }; expected=$2; shift 2 ;;
      *) apparmor_fail usage 'symphony runner apparmor [--apply --daemon-id ID]'; return 1 ;;
    esac
  done
  runtime=$(container_runtime)
  apparmor_daemon "$runtime" || { apparmor_fail daemon-host 'Cannot identify the selected daemon; inspect Docker context on the controller.'; return 1; }
  aa_profile=$(codex_apparmor_profile "$runtime" 2>/dev/null) || { apparmor_fail daemon-host 'Cannot inspect selected daemon security options. Check its Docker context and connection.'; return 1; }
  if [ "$aa_profile" = none ]; then printf 'AppArmor: selected daemon does not advertise AppArmor; no host changes required.\n'; return; fi
  codex_verify_profile "$symphony_home/scripts/codex-apparmor.profile" "$(codex_apparmor_id)" || return
  printf 'Policy: %s\nPolicy file: %s/scripts/codex-apparmor.profile\nSHA256: %s\nDestination: /etc/apparmor.d/%s\n' "$aa_profile" "$symphony_home" "$(codex_apparmor_id)" "$aa_profile"
  printf 'Administrator effects: install exact versioned policy and ownership receipt; load enforce mode; enable apparmor.service; add a versioned docker.service boot guard and daemon-reload. No restart or policy removal. Existing rollback profiles are retained.\n'
  apparmor_local_host || return
  if ! "$apply"; then
    printf 'Review the shipped policy and package inventory. To authorize these effects on this daemon, run as administrator using this physical package path and the same local Docker context:\n'
    printf '  %q runner apparmor --apply --daemon-id %q\n' "$symphony_home/scripts/symphony" "$aa_daemon"
    printf 'After preparation, run symphony runner doctor --agent codex --sandbox-only from the project as its ordinary user. Host preparation alone does not prove protected execution or authentication.\n'
    return
  fi
  [ "$expected" = "$aa_daemon" ] || { apparmor_fail daemon-host 'Explicit --daemon-id must match the reviewed selected daemon.'; return 1; }
  [ "$EUID" = 0 ] || { apparmor_fail privilege 'Explicit administrator authorization is required; rerun the reviewed command as root. Symphony never invokes sudo.'; return 1; }
  # Ignore caller-supplied executable paths for privileged effects.
  PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH
  umask 022
  apparmor_prerequisites || return
  apparmor_paths
  [[ "$aa_parser" =~ ^/[a-zA-Z0-9/_-]+$ ]] && apparmor_safe_path "$aa_parser" || { apparmor_fail tools 'Unsafe parser executable.'; return 1; }
  # Lock a root-owned directory, without creating/following a lock-file link.
  exec 9</etc/apparmor.d
  flock -x 9 || return
  aa_loaded=$(cat /sys/kernel/security/apparmor/profiles) || { apparmor_fail kernel 'Cannot inspect loaded policy inventory.'; return 1; }
  apparmor_conflicts || return
  # Stage bytes in root-owned storage and reverify after copying user package
  # input. Neither parser nor persistent destination consumes mutable user bytes.
  stage=$(mktemp -d /etc/apparmor.d/.symphony-stage.XXXXXX) || return
  trap 'rm -rf "$stage"' EXIT
  cp "$aa_policy" "$stage/policy"
  codex_verify_profile "$stage/policy" "$(codex_apparmor_id)" || return
  [ "$("$aa_parser" --names --skip-cache "$stage/policy")" = "$aa_profile" ] || { apparmor_fail integrity 'Compiled policy name differs.'; return 1; }
  "$aa_parser" --skip-kernel-load --skip-cache "$stage/policy" || { apparmor_fail imports 'Policy compilation failed; inspect kernel/parser/import compatibility.'; return 1; }
  # All conflicts and prerequisites checked before persistent mutation. Retain a
  # partial owned install on operational failure so an explicit retry is safe.
  install -m 0644 "$stage/policy" "$aa_destination"
  apparmor_receipt > "$aa_receipt"
  if [ ! -d "$aa_dropdir" ]; then mkdir "$aa_dropdir"; fi
  apparmor_boot_guard > "$aa_dropin"
  systemctl daemon-reload && systemctl enable apparmor.service || { apparmor_fail persistence 'Boot setup failed. Owned files retained; correct the service error and repeat the same authorized command before launch.'; return 1; }
  apparmor_verify_boot_guard || { apparmor_fail persistence 'Effective docker.service overrides omit the required boot guard. Administrator must resolve service overrides before launch.'; return 1; }
  # Even on repeat, reload the exact owned bytes; a listed name/disk hash does
  # not attest current kernel contents. Never replace an unowned loaded name.
  "$aa_parser" --replace --skip-cache "$aa_destination" || { apparmor_fail kernel 'Exact owned policy load failed; retry preparation after correcting the parser/kernel failure.'; return 1; }
  aa_loaded=$(cat /sys/kernel/security/apparmor/profiles) || return
  printf '%s\n' "$aa_loaded" | grep -Fx "$aa_profile (enforce)" >/dev/null || { apparmor_fail enforcement 'Exact policy was loaded but enforce mode was not observed.'; return 1; }
  printf 'AppArmor: exact owned policy loaded in enforce mode; persistent Docker boot guard installed. Docker was not restarted.\nNext: run symphony runner doctor --agent codex --sandbox-only as the ordinary project user. Protected execution, native auth and model delivery remain unproven by host setup.\n'
)

# Host diagnostics precede the actual protected project command. A remote host
# cannot be inspected through controller /proc or /etc; the protected check is
# still authoritative there, and a preparation failure is handed to that host.
apparmor_doctor() {
  local runtime=$1 aa_profile aa_endpoint aa_daemon aa_host aa_policy aa_destination aa_receipt aa_dropdir aa_dropin aa_parser aa_loaded
  aa_profile=$(codex_apparmor_profile "$runtime" 2>/dev/null) || { apparmor_fail daemon-host 'Cannot inspect selected daemon security options. Check its Docker context and connection.'; return 1; }
  [ "$aa_profile" != none ] || return 0
  codex_verify_profile "$symphony_home/scripts/codex-apparmor.profile" "$(codex_apparmor_id)" || return
  apparmor_daemon "$runtime" || { apparmor_fail daemon-host 'Cannot identify selected daemon.'; return 1; }
  if [ "$(uname -s)" != Linux ] || [ "$aa_endpoint" != unix:///var/run/docker.sock ] || [ "$aa_host" != "$(hostname)" ]; then
    printf 'AppArmor: remote daemon host inventory unavailable on controller. If protected execution fails, run runner apparmor using the verified package on the reported daemon host.\n'
    return
  fi
  if [ ! -r /sys/kernel/security/lsm ] || [ ! -r /sys/kernel/security/apparmor/profiles ]; then
    printf 'AppArmor: host kernel inventory requires administrator access; continuing to actual protected execution. If it fails, run symphony runner apparmor on this daemon host for explicit administrator preparation. No inventory readiness claim.\n'
    return
  fi
  apparmor_prerequisites || return
  apparmor_paths
  if ! aa_loaded=$(cat /sys/kernel/security/apparmor/profiles 2>/dev/null); then
    printf 'AppArmor: cannot read kernel inventory; continuing to actual protected execution. If it fails, run symphony runner apparmor on this daemon host for explicit administrator preparation. No inventory readiness claim.\n'; return
  fi
  if ! printf '%s\n' "$aa_loaded" | grep -Fx "$aa_profile (enforce)" >/dev/null; then
    apparmor_fail absent 'Shipped profile is absent or not enforced. Run symphony runner apparmor on this daemon host, review the plan and authorize preparation before retrying doctor.'; return 1
  fi
  apparmor_conflicts || return
  [ -f "$aa_receipt" ] && [ -f "$aa_dropin" ] || { apparmor_fail persistence 'Owned boot setup is incomplete. Repeat the explicitly authorized runner apparmor preparation.'; return 1; }
  apparmor_verify_boot_guard || { apparmor_fail persistence 'Effective Docker service boot ordering/load guard is missing. Repeat authorized preparation or resolve conflicting unit overrides.'; return 1; }
  printf 'AppArmor: owned persistent setup present and named profile enforced; kernel contents still require the following protected command check.\n'
}
