# Prefixed with the owned codex_config_boundary.py source before -I execution.
import json
import sys

LAUNCH_REFUSALS = frozenset(("sandbox_unqualified_config", "sandbox_unsupported_command",
                            "sandbox_toolchain_unqualified", "sandbox_launch_failed"))


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
        if str(Path(shutil.which(args[0]) or args[0]).resolve()) != executable:
            raise Refusal("sandbox_unqualified_config")
        chain = attest_runtime_chain(value, executable, require_launch_chain=operation != "probe")
        if operation == "probe":
            write_launch_receipt(value, chain)
        elif chain != value.get("expected_launch_chain"):
            raise Refusal("sandbox_unqualified_config")
        # Preserve the official frontend's package/environment semantics while
        # making interpreter selection explicit after trusted wrapper setup.
        if chain["node"] is None:
            os.execve(executable, [executable, *args[1:]], os.environ)
        node = chain["node"]["path"]
        os.execve(node, [node, executable, *args[1:]], os.environ)
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
