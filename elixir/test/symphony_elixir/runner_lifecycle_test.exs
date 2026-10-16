defmodule SymphonyElixir.RunnerLifecycleTest do
  use ExUnit.Case, async: true

  @launcher Path.expand("../../../scripts/symphony", __DIR__)

  setup do
    root = Path.join(System.tmp_dir!(), "symphony-runner-#{System.unique_integer([:positive])}")
    repo = Path.join(root, "repo")
    config = Path.join(root, "config")
    runtime = Path.join(root, "container-runtime")
    File.mkdir_p!(repo)
    File.mkdir_p!(Path.join(config, "projects"))
    File.mkdir_p!(Path.join(config, "runners"))
    {_, 0} = System.cmd("git", ["init", "-q", repo])
    {_, 0} = System.cmd("git", ["-C", repo, "remote", "add", "origin", "https://github.com/example/service.git"])
    File.write!(Path.join(repo, "WORKFLOW.md"), "fixture workflow")
    File.write!(Path.join(config, "projects/service.env"), "GH_TOKEN=fixture\n")

    File.write!(runtime, """
    #!/bin/bash
    set -eu
    printf '%s\\t' "$@" >> "$FIXTURE_LOG"
    printf '\\n' >> "$FIXTURE_LOG"
    case "$1" in
      inspect)
        [ -f "$FIXTURE_STATE" ] || { printf '\\n'; exit 1; }
        case "$*" in
          *State.Running*) cat "$FIXTURE_STATE" ;;
          *com.symphony.mode*) echo listener ;;
          *com.symphony.storage*) cat "$FIXTURE_STATE.storage" ;;
        esac ;;
      run)
        echo true > "$FIXTURE_STATE"
        for arg in "$@"; do
          case "$arg" in com.symphony.storage=*) printf '%s' "${arg#*=}" > "$FIXTURE_STATE.storage" ;; esac
        done ;;
      stop) echo false > "$FIXTURE_STATE" ;;
      rm) rm -f "$FIXTURE_STATE" ;;
    esac
    """)

    File.chmod!(runtime, 0o755)
    on_exit(fn -> File.rm_rf!(root) end)

    env = [
      {"SYMPHONY_CONFIG_HOME", config},
      {"SYMPHONY_RUNNER_HOME", Path.join(config, "runners")},
      {"SYMPHONY_CONTAINER_RUNTIME", runtime},
      {"FIXTURE_LOG", Path.join(root, "calls")},
      {"FIXTURE_STATE", Path.join(root, "running")}
    ]

    %{repo: repo, env: env, log: Path.join(root, "calls")}
  end

  test "missing container inspection discards stdout before reporting or creating", ctx do
    assert {"Isolated worker: not running\n", 0} = launch(ctx, "status")
    assert {_, 0} = launch(ctx, "start")

    calls = File.read!(ctx.log) |> String.split("\n", trim: true)
    assert Enum.any?(calls, &String.starts_with?(&1, "run\t"))
    refute Enum.any?(calls, &String.starts_with?(&1, "rm\t"))
    refute Enum.any?(calls, &(String.starts_with?(&1, "inspect\t") and String.contains?(&1, "com.symphony.storage")))
  end

  test "runner recreation reuses repository volumes and stops gracefully", ctx do
    assert {_, 0} = launch(ctx, "start")
    assert {status, 0} = launch(ctx, "status")
    assert status =~ "Workspaces: /workspaces (volume symphony-service-"
    assert {_, 0} = launch(ctx, "stop")
    assert {_, 0} = launch(ctx, "start")

    calls = File.read!(ctx.log) |> String.split("\n", trim: true)
    runs = Enum.filter(calls, &String.starts_with?(&1, "run\t"))
    assert [first, second] = runs
    assert first == second
    assert first =~ "--restart\tunless-stopped\t"
    refute first =~ "--rm\t"
    assert first =~ "-workspaces:/workspaces\t"
    assert first =~ "-state:/var/lib/symphony\t"
    assert Enum.any?(calls, &String.starts_with?(&1, "stop\t--time\t30\t"))
    stop_index = Enum.find_index(calls, &String.starts_with?(&1, "stop\t"))
    remove_index = Enum.find_index(calls, &String.starts_with?(&1, "rm\t"))
    assert stop_index < remove_index
    refute Enum.any?(calls, &String.starts_with?(&1, "volume\trm\t"))
  end

  test "storage follows repository identity across transport changes and separates owners", ctx do
    assert {_, 0} = launch(ctx, "start")
    assert {_, 0} = launch(ctx, "stop")
    {_, 0} = System.cmd("git", ["remote", "set-url", "origin", "git@github.com:example/service.git"], cd: ctx.repo)
    assert {_, 0} = launch(ctx, "start")
    {_, 0} = System.cmd("git", ["remote", "set-url", "origin", "https://github.com/another/service.git"], cd: ctx.repo)
    assert {_, 1} = launch(ctx, "start")
    assert {_, 1} = launch(ctx, "status")
    assert {_, 1} = launch(ctx, "stop")
    {_, 0} = System.cmd("git", ["remote", "set-url", "origin", "ssh://git@github.com/example/service.git"], cd: ctx.repo)
    assert {_, 0} = launch(ctx, "stop")
    assert {_, 0} = launch(ctx, "start")
    assert {_, 0} = launch(ctx, "stop")
    {_, 0} = System.cmd("git", ["remote", "set-url", "origin", "https://github.com/another/service.git"], cd: ctx.repo)
    assert {_, 0} = launch(ctx, "start")

    [first, second, third, fourth] =
      ctx.log
      |> File.read!()
      |> String.split("\n")
      |> Enum.filter(&(String.starts_with?(&1, "volume\tcreate\t") and String.contains?(&1, "-workspaces")))

    assert first == second
    assert first == third
    refute first == fourth
  end

  defp launch(ctx, command) do
    System.cmd("bash", [@launcher, "runner", command], cd: ctx.repo, env: ctx.env, stderr_to_stdout: true)
  end
end
