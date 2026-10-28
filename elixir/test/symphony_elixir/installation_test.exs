defmodule SymphonyElixir.InstallationTest do
  use ExUnit.Case, async: true

  @launcher Path.expand("../../../scripts/symphony", __DIR__)
  @git_env [{"GIT_CONFIG_GLOBAL", "/dev/null"}, {"GIT_CONFIG_NOSYSTEM", "1"}, {"GIT_CONFIG_COUNT", nil}]

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-install-#{System.pid()}-#{System.unique_integer([:positive])}")
    source = Path.join(root, "source")
    tools = Path.join(root, "tools")
    File.mkdir_p!(Path.join(source, "scripts"))
    File.mkdir_p!(Path.join(source, "elixir"))
    File.mkdir_p!(tools)
    # Model an installation with no host agent CLIs even on CI images that have gh.
    [definitions, dispatch] = String.split(File.read!(@launcher), "\ncase \"${1:-}\" in", parts: 2)
    availability = "\ntool_available() { case \"$1\" in gh|codex) return 1 ;; *) command -v \"$1\" >/dev/null 2>&1 ;; esac; }\n"
    File.write!(Path.join(source, "scripts/symphony"), definitions <> availability <> "\ncase \"${1:-}\" in" <> dispatch)

    File.cp!(Path.join(Path.dirname(@launcher), "symphony-environment.sh"), Path.join(source, "scripts/symphony-environment.sh"))
    File.cp!(Path.join(Path.dirname(@launcher), "symphony-apparmor.sh"), Path.join(source, "scripts/symphony-apparmor.sh"))

    for skill <- ~w(commit push pull linear land), do: File.mkdir_p!(Path.join(source, ".codex/skills/#{skill}"))

    File.chmod!(Path.join(source, "scripts/symphony"), 0o755)

    executable!(Path.join(tools, "mise"), """
    #!/bin/bash
    printf '%s\\n' "$*" >> "$TEST_CALLS"
    case "$*" in
      *'exec -- erl '*) exit "${TEST_ERLANG_FAILURE:-0}" ;;
      *'exec -- mix setup')
        if [ -f "$TEST_FAIL_SETUP" ]; then rm "$TEST_FAIL_SETUP"; exit 47; fi ;;
      *'exec -- mix build')
        [ "${TEST_NO_BUILD:-false}" = false ] || exit 0
        mkdir -p "$SYMPHONY_HOME/elixir/bin"
        printf '#!/bin/bash\\nprintf "host executed: %%s\\\\n" "$*"\\n' > "$SYMPHONY_HOME/elixir/bin/symphony"
        chmod +x "$SYMPHONY_HOME/elixir/bin/symphony" ;;
      *'exec -C '*) shift 3; exec "$@" ;;
    esac
    """)

    executable!(Path.join(tools, "docker"), "#!/bin/bash\nexit 0\n")
    File.write!(Path.join(source, ".gitignore"), "elixir/bin/\n")
    git!(source, ["init", "--initial-branch=main"])
    git!(source, ["add", "."])
    git!(source, ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "Fixture installation"])

    env =
      @git_env ++
        [
          {"PATH", tools <> ":/usr/bin:/bin"},
          {"HOME", Path.join(root, "home")},
          {"SYMPHONY_HOME", source},
          {"SYMPHONY_CONFIG_HOME", Path.join(root, "config")},
          {"SYMPHONY_BIN_DIR", Path.join(root, "bin")},
          {"SYMPHONY_SKILLS_DIR", Path.join(root, "skills")},
          {"SYMPHONY_CONTAINER_RUNTIME", Path.join(tools, "docker")},
          {"TEST_CALLS", Path.join(root, "calls")},
          {"TEST_FAIL_SETUP", Path.join(root, "fail-setup")}
        ]

    on_exit(fn -> File.rm_rf!(root) end)
    %{root: root, source: source, tools: tools, env: env}
  end

  test "default source setup builds without host Codex or gh", ctx do
    assert {output, 0} = cli(ctx, ["setup"])
    assert output =~ "Runtime setup complete"
    calls = File.read!(Path.join(ctx.root, "calls"))
    assert calls =~ " install\n"
    assert calls =~ "exec -- erl"
    assert calls =~ "exec -- mix setup"
    assert calls =~ "exec -- mix build"
    assert File.exists?(Path.join(ctx.source, "elixir/bin/symphony"))
  end

  test "missing source build tool still blocks setup", ctx do
    File.rm!(Path.join(ctx.tools, "mise"))
    assert {output, 1} = cli(ctx, ["setup"])
    assert output =~ "Missing prerequisite: mise"
  end

  test "automatic prerequisites install only the source build tool", ctx do
    template = Path.join(ctx.root, "mise-template")
    File.rename!(Path.join(ctx.tools, "mise"), template)
    executable!(Path.join(ctx.tools, "uname"), "#!/bin/bash\nprintf Darwin\n")

    executable!(Path.join(ctx.tools, "brew"), """
    #!/bin/bash
    printf '%s\\n' "$*" >> "$TEST_CALLS"
    [ "$*" = 'install mise' ] || exit 91
    cp "$TEST_MISE_TEMPLATE" "$(dirname "$0")/mise"
    """)

    assert {_, 0} = cli(ctx, ["setup", "--install-prerequisites"], [{"TEST_MISE_TEMPLATE", template}])
    calls = File.read!(Path.join(ctx.root, "calls"))
    assert calls =~ "install mise"
    refute calls =~ "install gh"
    refute calls =~ "codex"
  end

  test "source setup rejects failed runtime probe and missing build output", ctx do
    assert {output, 1} = cli(ctx, ["setup"], [{"TEST_ERLANG_FAILURE", "1"}])
    assert output =~ "Erlang runtime is unavailable"
    assert {output, 1} = cli(ctx, ["setup"], [{"TEST_NO_BUILD", "true"}])
    assert output =~ "without creating elixir/bin/symphony"
  end

  test "container doctor needs installed source and container runtime, not host execution", ctx do
    assert {_, 0} = cli(ctx, ["install"])
    assert {output, 1} = cli(ctx, ["doctor", "--json"])
    assert output =~ ~s("source":false)
    assert {_, 0} = cli(ctx, ["setup"])
    File.rm!(Path.join(ctx.tools, "mise"))
    assert {output, 0} = cli(ctx, ["doctor", "--json"])
    assert output =~ ~s("host":false,"ready":true)
    assert output =~ ~s("container":true)
    File.rm!(Path.join(ctx.tools, "docker"))
    assert {output, 1} = cli(ctx, ["doctor", "--json"])
    assert output =~ ~s("ready":false)
  end

  test "host doctor does not require a container runtime and checks host execution", ctx do
    assert {_, 0} = cli(ctx, ["install"])
    assert {_, 0} = cli(ctx, ["setup"])
    File.rm!(Path.join(ctx.tools, "docker"))
    assert {output, 0} = cli(ctx, ["doctor", "--host", "--json"])
    assert output =~ ~s("host":true,"ready":true)
    assert {output, 1} = cli(ctx, ["doctor", "--host", "--json"], [{"TEST_ERLANG_FAILURE", "1"}])
    assert output =~ ~s("runtime":false)
  end

  test "supported host run executes the installed runtime and Claude host remains refused", ctx do
    assert {_, 0} = cli(ctx, ["setup"])
    config = Path.join(ctx.root, "config/projects")
    File.mkdir_p!(config)
    {identity, 0} = System.cmd("bash", ["-c", ~s(source "$1" --help >/dev/null; project_id "$2"), "fixture", @launcher, git!(ctx.source, ["rev-parse", "--show-toplevel"])])
    File.write!(Path.join(config, String.trim(identity) <> ".env"), "")
    File.write!(Path.join(ctx.source, "WORKFLOW.md"), "fixture")
    assert {output, 0} = cli(ctx, ["run", "--host"])
    assert output =~ "host executed:"
    assert output =~ "--agent codex"
    assert {output, 1} = cli(ctx, ["run", "--host", "--agent", "claude"])
    assert output =~ "Claude host/SSH execution is unsupported"
  end

  test "source update retries failed real setup at the installed revision without host agents", ctx do
    remote = Path.join(ctx.root, "upstream.git")
    git!(ctx.root, ["clone", "--bare", ctx.source, remote])
    git!(ctx.source, ["remote", "add", "origin", remote])
    git!(ctx.source, ["fetch", "origin", "main"])
    git!(ctx.source, ["branch", "--set-upstream-to=origin/main", "main"])
    seed = Path.join(ctx.root, "seed")
    git!(ctx.root, ["clone", remote, seed])
    File.write!(Path.join(seed, "revision.txt"), "new source")
    git!(seed, ["add", "revision.txt"])
    git!(seed, ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "New source"])
    git!(seed, ["push", "origin", "main"])
    File.write!(Path.join(ctx.root, "fail-setup"), "fail once")
    revision = git!(seed, ["rev-parse", "HEAD"])
    refute git!(ctx.source, ["rev-parse", "HEAD"]) == revision
    assert {_, 47} = cli(ctx, ["update"])
    assert git!(ctx.source, ["rev-parse", "HEAD"]) == revision
    refute File.exists?(Path.join(ctx.source, "elixir/bin/symphony"))
    assert {output, 0} = cli(ctx, ["update"])
    assert output =~ "Updated: #{revision} -> #{revision}"
    assert File.exists?(Path.join(ctx.source, "elixir/bin/symphony"))
    assert {_, 0} = cli(ctx, ["doctor", "--json"])
    assert length(String.split(File.read!(Path.join(ctx.root, "calls")), "exec -- mix setup")) == 3
  end

  defp cli(ctx, args, extra_env \\ []), do: System.cmd("bash", [Path.join(ctx.source, "scripts/symphony") | args], cd: ctx.source, env: ctx.env ++ extra_env, stderr_to_stdout: true)

  defp executable!(path, contents) do
    File.write!(path, contents)
    File.chmod!(path, 0o755)
  end

  defp git!(root, args) do
    {output, 0} = System.cmd("git", args, cd: root, env: @git_env, stderr_to_stdout: true)
    String.trim(output)
  end
end
