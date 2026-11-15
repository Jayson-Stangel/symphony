# Prefixed with the owned codex_config_boundary.py source before -I execution.
import json
import sys

LAUNCH_REFUSALS = frozenset(("sandbox_unqualified_config", "sandbox_unsupported_command",
                            "sandbox_toolchain_unqualified", "sandbox_launch_failed"))


def browser_environment(value):
    env = os.environ.copy()
    workspace = _absolute(value["workspace"])
    # Crashpad uses Chrome's default config directory even with --user-data-dir.
    # Supply a task-local fallback without redirecting XDG state for other tools.
    chrome_home = env.get("CHROME_CONFIG_HOME")
    xdg_home = env.get("XDG_CONFIG_HOME")
    if not chrome_home and xdg_home:
        env.pop("CHROME_CONFIG_HOME", None)
        selected = xdg_home
    else:
        selected = chrome_home or str(workspace / ".symphony-browser")
        env["CHROME_CONFIG_HOME"] = selected

    try:
        selected.encode("utf-8")
    except UnicodeError:
        refuse()
    # Chromium rejects these noncharacters too and would fall through to XDG.
    if any(0xFDD0 <= ord(char) <= 0xFDEF or ord(char) & 0xFFFF >= 0xFFFE for char in selected):
        refuse()
    path = _absolute(selected)
    if path != workspace and workspace not in path.parents:
        refuse()
    parts = path.relative_to(workspace).parts
    if {".git", ".codex", ".agents"}.intersection(parts):
        refuse()
    for key in ("expected_home", "expected_codex_home"):
        protected = _absolute(value[key])
        if path == protected or protected in path.parents or path in protected.parents:
            refuse()
    current = workspace
    for part in parts:
        current = current / part
        try:
            current.lstat()
        except FileNotFoundError:
            break
        _directory(current, os.getuid())
    return env


def write_launch_receipt(value, receipt):
    fd = value.get("receipt_fd")
    if type(fd) is not int or fd < 3 or fd > 1024:
        raise Refusal("sandbox_unqualified_config")
    data = json.dumps(receipt, sort_keys=True).encode()
    if len(data) > min(16384, os.fpathconf(fd, "PC_PIPE_BUF")):
        raise Refusal("sandbox_unqualified_config")
    try:
        os.set_blocking(fd, False)
        if os.write(fd, data) != len(data):
            raise Refusal("sandbox_unqualified_config")
    finally:
        os.close(fd)


def launch_main():
    value = {}
    try:
        value = json.loads(sys.argv[1])
        operation = value.get("operation", "exec")
        if operation not in ("exec", "probe"):
            raise Refusal("sandbox_unqualified_config")
        args = sys.argv[2:]
        if not args:
            raise Refusal("sandbox_unsupported_command")
        if operation == "exec" and (args[-1:] != ["app-server"] or args[1:-1] not in
                ([], ["--config", "shell_environment_policy.inherit=all"],
                 ["-c", "shell_environment_policy.inherit=all"])):
            raise Refusal("sandbox_unsupported_command")
        context = attest_context(value, require_snapshot=operation != "probe")
        executable = context["executable"]
        try:
            if str(Path(shutil.which(args[0]) or args[0]).resolve()) != executable:
                raise Refusal("sandbox_toolchain_unqualified")
        except (OSError, RuntimeError, ValueError, TypeError):
            raise Refusal("sandbox_toolchain_unqualified")
        chain = attest_runtime_chain(value, executable, require_launch_chain=operation != "probe")
        if operation == "probe":
            write_launch_receipt(value, chain)
            env = os.environ
        elif chain != value.get("expected_launch_chain"):
            raise Refusal("sandbox_toolchain_unqualified")
        else:
            env = browser_environment(value)
        # Preserve the official frontend's package/environment semantics while
        # making interpreter selection explicit after trusted wrapper setup.
        if chain["node"] is None:
            os.execve(executable, [executable, *args[1:]], env)
        node = chain["node"]["path"]
        os.execve(node, [node, executable, *args[1:]], env)
    except Exception as error:
        code = str(error) if isinstance(error, Refusal) and str(error) in LAUNCH_REFUSALS else "sandbox_launch_failed"
        # This private pipe carries only fixed codes, never child output or
        # exception details. Successful receipts keep their existing chain schema.
        if isinstance(value, dict) and value.get("operation") == "probe":
            try:
                write_launch_receipt(value, {"error": code})
            except Exception:
                pass
        print("symphony: " + code + " before native launch", file=sys.stderr)
        return 78


if __name__ == "__main__":
    sys.exit(launch_main())
