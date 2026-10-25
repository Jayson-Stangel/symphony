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
