# Prefixed with the owned codex_config_boundary.py source before -I execution.
import json
import sys
def launch_main():
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
            fd = value.get("receipt_fd")
            if type(fd) is not int or fd < 3 or fd > 1024:
                raise Refusal("sandbox_unqualified_config")
            data = json.dumps(chain, sort_keys=True).encode()
            if len(data) > min(16384, os.fpathconf(fd, "PC_PIPE_BUF")):
                raise Refusal("sandbox_unqualified_config")
            try:
                os.set_blocking(fd, False)
                if os.write(fd, data) != len(data):
                    raise Refusal("sandbox_unqualified_config")
            finally:
                os.close(fd)
        elif chain != value.get("expected_launch_chain"):
            raise Refusal("sandbox_unqualified_config")
        # Preserve the official frontend's package/environment semantics while
        # making interpreter selection explicit after trusted wrapper setup.
        if chain["node"] is None:
            os.execve(executable, [executable, *args[1:]], os.environ)
        node = chain["node"]["path"]
        os.execve(node, [node, executable, *args[1:]], os.environ)
    except Exception:
        print("symphony: sandbox_unqualified_config before native launch", file=sys.stderr)
        return 78


if __name__ == "__main__":
    sys.exit(launch_main())
