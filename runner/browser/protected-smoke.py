"""Exercise Crashpad under the packaged managed Codex app-server."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_npm_cache import NativeCommands


def main():
    home = Path("/root")
    native = home / ".codex"
    config = native / "config.toml"
    workspace = Path("/workspaces/browser")
    workspace.mkdir(mode=0o700)
    workflow = Path("/tmp/protected-browser-WORKFLOW.md")
    workflow.write_text("---\ntracker:\n  kind: memory\nworkspace:\n  root: /workspaces\n"
                        "codex:\n  command: bash browser-wrapper.sh codex app-server\n---\n")
    (native / "auth.json").write_text("{}")
    os.environ.update(HOME=str(home), CODEX_HOME=str(native), MIX_ENV="prod",
                      SYMPHONY_CODEX_CONFIG_PROFILE="trust-ro-v2",
                      SYMPHONY_CODEX_CONFIG_SHA256=hashlib.sha256(b"").hexdigest(),
                      SYMPHONY_CODEX_CONFIG_OWNER=str(config.stat().st_uid))
    chrome, = Path("/opt/symphony/ms-playwright").glob("chromium-*/chrome-linux*/chrome")
    for label, environment, state in (
        ("default", "unset CHROME_CONFIG_HOME XDG_CONFIG_HOME", ".symphony-browser"),
        ("Chrome override", 'export CHROME_CONFIG_HOME="$PWD/config/chrome" XDG_CONFIG_HOME="$PWD/config/ignored"',
         "config/chrome"),
        ("XDG workspace root", 'unset CHROME_CONFIG_HOME\nexport XDG_CONFIG_HOME="$PWD"', "."),
    ):
        project = workspace / label.replace(" ", "-")
        project.mkdir(mode=0o700)
        wrapper = project / "browser-wrapper.sh"
        setup = f"#!/bin/bash\nset -eu\n{environment}\n"
        wrapper.write_text(setup + 'exec "$@"\n')
        state = project / state
        browser_launch(chrome, home, native, project, workflow, state, interrupt=True)
        socket, = state.glob("*-headless/scoped_dir*/SingletonSocket")
        profile = socket.parent
        retained = {name: os.readlink(profile / name)
                    for name in ("SingletonSocket", "SingletonLock", "SingletonCookie")}
        # Check preservation from inside the actual wrapper's disposable run.
        relative = shlex.quote(str((profile / "project-file").relative_to(project)))
        (profile / "project-file").write_text("preserve me")
        wrapper.write_text(setup + f'test "$(cat {relative})" = "preserve me"\nexec "$@"\n')
        browser_launch(chrome, home, native, project, workflow, state)
        assert {name: os.readlink(profile / name) for name in retained} == retained
        print(f"{label}: protected Chromium cold start and wrapped restart passed.", flush=True)


def browser_launch(chrome, home, native, workspace, workflow, state, *, interrupt=False):
    launch = subprocess.check_output(
        ["mix", "run", "--no-start", "--no-compile", "--no-deps-check", "-e", """
        alias SymphonyElixir.{Config, Workflow}
        alias SymphonyElixir.Codex.SandboxReadiness
        [workspace, workflow] = System.argv()
        Workflow.set_workflow_file_path(workflow)
        {:ok, workflow} = Workflow.load()
        {:ok, settings} = Config.Schema.parse(workflow.config)
        {:ok, command} = SandboxReadiness.qualified_command(settings.codex.command, workspace, settings)
        IO.puts(Jason.encode!(command))
        """, "--", str(workspace), str(workflow)],
        cwd="/opt/symphony/elixir", text=True, timeout=195)
    # NativeCommands uses the same synthetic path for HOME and CODEX_HOME.
    # Restore the normal packaged split before executing the attested command.
    server = NativeCommands(home, workspace, ("env", f"CODEX_HOME={native}",
                                               *shlex.split(json.loads(launch.splitlines()[-1]))))
    server.start()
    try:
        policy = {"type": "workspaceWrite", "writableRoots": [str(workspace)], "networkAccess": True}
        command = [str(chrome), "--headless", "--no-sandbox", "--disable-dev-shm-usage", "--no-first-run"]
        if interrupt:
            # Wait for this browser's real singleton links, then simulate an
            # abrupt stop so its ordinary cleanup cannot remove them.
            command = [sys.executable, "-c", """
import pathlib, subprocess, sys, time
state = pathlib.Path(sys.argv[1])
child = subprocess.Popen(sys.argv[2:], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and child.poll() is None:
        for socket in state.glob("*-headless/scoped_dir*/SingletonSocket"):
            if all((socket.parent / name).is_symlink()
                   for name in ("SingletonSocket", "SingletonLock", "SingletonCookie")):
                sys.exit(0)
        time.sleep(0.05)
    raise SystemExit("Chromium did not create its implicit headless profile links")
finally:
    if child.poll() is None:
        child.kill()
    child.wait()
""", str(state), *command, "--remote-debugging-port=0", "about:blank"]
        else:
            command += ["--dump-dom", "about:blank"]
        result = server.request("command/exec", {
            "command": command,
            "cwd": str(workspace), "timeoutMs": 20000, "sandboxPolicy": policy,
        })
        assert result["exitCode"] == 0, result
        if not interrupt:
            assert "<html>" in result["stdout"], result
        database, = state.glob("*/Crash Reports/settings.dat")
        assert database.is_file()
        assert not list((home / ".config").glob("*/Crash Reports/settings.dat"))
        print("Protected Chromium startup and task-owned Crashpad database passed.", flush=True)
    finally:
        server.close()


if __name__ == "__main__":
    main()
