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
    workflow.write_text("---\ntracker:\n  kind: memory\nworkspace:\n  root: /workspaces\n---\n")
    (native / "auth.json").write_text("{}")
    os.environ.update(HOME=str(home), CODEX_HOME=str(native), MIX_ENV="prod",
                      SYMPHONY_CODEX_CONFIG_PROFILE="trust-ro-v2",
                      SYMPHONY_CODEX_CONFIG_SHA256=hashlib.sha256(b"").hexdigest(),
                      SYMPHONY_CODEX_CONFIG_OWNER=str(config.stat().st_uid))
    for name in ("CHROME_CONFIG_HOME", "XDG_CONFIG_HOME"):
        os.environ.pop(name, None)
    launch = subprocess.check_output(
        ["mix", "run", "--no-start", "--no-compile", "--no-deps-check", "-e", """
        alias SymphonyElixir.{Config, Workflow}
        alias SymphonyElixir.Codex.SandboxReadiness
        [workspace, workflow] = System.argv()
        Workflow.set_workflow_file_path(workflow)
        {:ok, workflow} = Workflow.load()
        {:ok, settings} = Config.Schema.parse(workflow.config)
        {:ok, command} = SandboxReadiness.qualified_command("codex app-server", workspace, settings)
        IO.puts(Jason.encode!(command))
        """, "--", str(workspace), str(workflow)],
        cwd="/opt/symphony/elixir", text=True, timeout=195)
    # NativeCommands uses the same synthetic path for HOME and CODEX_HOME.
    # Restore the normal packaged split before executing the attested command.
    server = NativeCommands(home, workspace, ("env", f"CODEX_HOME={native}",
                                               *shlex.split(json.loads(launch.splitlines()[-1]))))
    chrome, = Path("/opt/symphony/ms-playwright").glob("chromium-*/chrome-linux*/chrome")
    server.start()
    try:
        policy = {"type": "workspaceWrite", "writableRoots": [str(workspace)], "networkAccess": True}
        result = server.request("command/exec", {
            "command": [str(chrome), "--headless", "--no-sandbox", "--disable-dev-shm-usage",
                        "--no-first-run", f"--user-data-dir={workspace / 'profile'}",
                        "--dump-dom", "about:blank"],
            "cwd": str(workspace), "timeoutMs": 20000, "sandboxPolicy": policy,
        })
        assert result["exitCode"] == 0 and "<html>" in result["stdout"], result
        state = workspace / ".symphony-browser"
        assert list(state.glob("*/Crash Reports/settings.dat")), list(state.glob("**/*"))
        assert not list((home / ".config").glob("*/Crash Reports/settings.dat"))
        print("Protected Chromium startup and task-owned Crashpad database passed.", flush=True)
    finally:
        server.close()


if __name__ == "__main__":
    main()
