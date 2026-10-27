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
# Capture the line as an argument before evaluating a compound trap body.
# Bash 5 resets LINENO inside that body and sends a synthetic line 1 on EXIT.
# Package scripts reserve line 1 for their shebang; 0 means unknown context.
package_diagnostics_line=0
package_diagnostics_debug() {
  if [ "$1" -gt 1 ]; then package_diagnostics_line=$1; fi
}
if [ -n "${SYMPHONY_MACOS_PACKAGE_DIAGNOSTICS:-}" ]; then
  trap 'package_diagnostics_debug "$LINENO"' DEBUG
fi
package_diagnostics_cleanup() {
  local status=$1
  if rm -rf "$stage"; then :
  elif [ "$status" = 0 ]; then status=1
  fi
  package_diagnostics_exit "$status" "$package_diagnostics_line"
}
