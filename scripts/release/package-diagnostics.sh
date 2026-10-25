# Sourced by package builders. Fixed stage labels only; never enable shell tracing.
package_diagnostics_current=''
package_diagnostics_tool="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/package_diagnostics.py"
package_diagnostics_record() {
  if [ -n "${SYMPHONY_MACOS_PACKAGE_DIAGNOSTICS:-}" ]; then
    python3 "$package_diagnostics_tool" "$@"
  fi
}
package_stage() {
  if [ -n "$package_diagnostics_current" ]; then
    package_diagnostics_record "$package_diagnostics_current" success
  fi
  package_diagnostics_current=$1
  package_diagnostics_record "$package_diagnostics_current" running
}
package_diagnostics_exit() {
  local status=$1
  local line=${2:-0}
  if [ -n "$package_diagnostics_current" ]; then
    if [ "$status" = 0 ]; then
      package_diagnostics_record "$package_diagnostics_current" success || status=1
    else
      # A receipt failure must not replace the command's original exit status.
      package_diagnostics_record "$package_diagnostics_current" failed "$status" "$line" || :
    fi
  fi
  return "$status"
}
# Capture the numeric location before execution. EXIT's LINENO differs between
# Bash 3 and Bash 5. Do not overwrite the last command's location on trap entry.
package_diagnostics_line=0
if [ -n "${SYMPHONY_MACOS_PACKAGE_DIAGNOSTICS:-}" ]; then
  trap 'case "$BASH_COMMAND" in package_diagnostics_exit*|package_diagnostics_cleanup*) ;; *) package_diagnostics_line=$LINENO ;; esac' DEBUG
fi
package_diagnostics_cleanup() {
  local status=$1
  if rm -rf "$stage"; then :
  elif [ "$status" = 0 ]; then status=1
  fi
  package_diagnostics_exit "$status" "$package_diagnostics_line"
}
