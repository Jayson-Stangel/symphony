defmodule SymphonyElixir.FreshSessionTest do
  use SymphonyElixir.TestSupport

  alias SymphonyElixir.{AgentRuntimeSupervisor, Config.Schema}

  @active_states ["In Progress", "Agent Review", "Verification", "Retest"]

  setup do
    root = Path.dirname(Workflow.workflow_file_path())
    workspace_root = Path.join(root, "workspaces")
    workspace = Path.join(workspace_root, "FRESH-1")
    executable = Path.join(root, "codex-fixture")
    issue = %Issue{id: "fresh-one", identifier: "FRESH-1", title: "Issue fixture", state: "In Progress", dispatchable: true}
    File.mkdir_p!(workspace)
    File.write!(Path.join(workspace, "untracked-notes"), "retain me")
    write_codex_fixture(executable)
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [])

    config = [
      tracker_kind: "memory",
      prompt: "Issue {{ issue.title }} is {{ issue.state }}.",
      tracker_active_states: @active_states,
      tracker_blocked_states: ["Blocked"],
      workspace_root: workspace_root,
      codex_command: "#{executable} app-server",
      codex_stall_timeout_ms: 0,
      poll_interval_ms: 60_000,
      max_concurrent_agents: 1
    ]

    %{config: config, issue: issue, workspace: workspace}
  end

  test "configuration normalizes status names and rejects malformed entries", ctx do
    for {input, expected} <- [{[], []}, {[" Agent Review ", "agent review", "Verification"], ["agent review", "verification"]}] do
      write_workflow_file!(Workflow.workflow_file_path(), Keyword.put(ctx.config, :fresh_session_on_enter, input))
      assert {:ok, %{agent: %{fresh_session_on_enter: ^expected}}} = Config.settings()
    end

    for invalid <- [nil, "Agent Review", [42], [nil], ["   "]] do
      write_workflow_file!(Workflow.workflow_file_path(), Keyword.put(ctx.config, :fresh_session_on_enter, invalid))
      assert {:ok, workflow} = Workflow.load()
      assert {:error, reason} = Schema.parse(workflow.config)
      assert inspect(reason) =~ "fresh_session_on_enter"
    end
  end

  test "polling during an active turn replaces once per configured entry and reuses the workspace", ctx do
    fixture = start_runner(ctx, fresh_session_on_enter: ["agent review", "Verification"])
    first = await_turn(ctx, 1)

    move(fixture, " agent REVIEW ")
    second = await_turn(ctx, 2)
    assert first["thread"] != second["thread"]
    assert first["pid"] != second["pid"]
    assert first["cwd"] == second["cwd"]
    assert second["prior_exit"]
    assert File.read!(Path.join(ctx.workspace, "untracked-notes")) == "retain me"

    for status <- ["Agent Review", "Retest", "Retest"] do
      move(fixture, status)
      assert running(fixture).agent_session.native.thread_id == second["thread"]
    end

    move(fixture, "Agent Review")
    third = await_turn(ctx, 3)
    move(fixture, "Verification")
    fourth = await_turn(ctx, 4)
    assert MapSet.size(MapSet.new([first, second, third, fourth], & &1["thread"])) == 4
    assert Enum.all?([second, third, fourth], &String.contains?(&1["prompt"], "Issue fixture"))
  end

  test "the between-turn refresh applies a configured entry before a second turn", ctx do
    fixture = start_runner(ctx, fresh_session_on_enter: ["Agent Review"])
    first = await_turn(ctx, 1)
    set_issue(ctx, "Agent Review")
    File.touch!(Path.join(ctx.workspace, "finish-#{first["pid"]}"))

    second = await_turn(ctx, 2)
    assert second["thread"] != first["thread"]
    assert second["turn"] == 1
    assert second["prior_exit"]
    assert running(fixture).agent_session.native.thread_id == second["thread"]
  end

  test "opt-out continues the same session until a human moves the issue to Blocked", ctx do
    fixture = start_runner(ctx)
    first = await_turn(ctx, 1)
    set_issue(ctx, "Agent Review")
    File.touch!(Path.join(ctx.workspace, "finish-#{first["pid"]}"))
    second = await_turn(ctx, 2)
    assert second["thread"] == first["thread"]
    assert second["turn"] == 2

    move(fixture, "Blocked")
    await(fn -> File.exists?(Path.join(ctx.workspace, "stopped-#{first["pid"]}")) end)
    await(fn -> running(fixture) == nil and not claimed?(fixture) end)
    for _ <- 1..3, do: move(fixture, "Blocked")
    assert length(turns(ctx)) == 2
  end

  test "an unconfirmed stop holds the claim, then the latest tracker state controls admission", ctx do
    fixture = start_runner(ctx, fresh_session_on_enter: ["Agent Review"])
    first = await_turn(ctx, 1)
    hold = Path.join(ctx.workspace, "hold-exit-#{first["pid"]}")
    File.touch!(hold)

    move(fixture, "Agent Review")
    await(fn -> running(fixture)[:ref] == nil end)
    assert running(fixture).tracker_stop.reason == :fresh_session
    assert claimed?(fixture)
    assert length(turns(ctx)) == 1

    move(fixture, "Blocked")
    assert claimed?(fixture)
    File.rm!(hold)
    await(fn -> running(fixture) == nil and not claimed?(fixture) end)
    for _ <- 1..3, do: move(fixture, "Blocked")
    assert length(turns(ctx)) == 1

    move(fixture, "Retest")
    replacement = await_turn(ctx, 2)
    assert first["thread"] != replacement["thread"]
    assert first["cwd"] == replacement["cwd"]
  end

  test "failed replacement tracker refresh keeps the claim until a successful current read", ctx do
    fixture = start_runner(ctx, fresh_session_on_enter: ["Agent Review"], max_retry_backoff_ms: 200)
    first = await_turn(ctx, 1)
    hold = Path.join(ctx.workspace, "hold-exit-#{first["pid"]}")
    File.touch!(hold)
    move(fixture, "Agent Review")
    await(fn -> File.exists?(Path.join(ctx.workspace, "stopped-#{first["pid"]}")) end)

    {:ok, socket} = :gen_tcp.listen(0, [:inet, ip: {127, 0, 0, 1}])
    {:ok, port} = :inet.port(socket)
    :ok = :gen_tcp.close(socket)

    write_workflow_file!(
      Workflow.workflow_file_path(),
      Keyword.merge(fixture.config, tracker_kind: "linear", tracker_endpoint: "http://127.0.0.1:#{port}/graphql")
    )

    File.rm!(hold)
    await(fn -> match?(%{error: "retry poll failed:" <> _}, :sys.get_state(fixture.server).retry_attempts[ctx.issue.id]) end)
    assert claimed?(fixture)
    assert length(turns(ctx)) == 1

    set_issue(ctx, "Verification")
    write_workflow_file!(Workflow.workflow_file_path(), fixture.config)
    second = await_turn(ctx, 2)
    assert first["thread"] != second["thread"]
    assert running(fixture).issue.state == "Verification"
  end

  test "replacement renders the latest tracker issue after native initialization", ctx do
    fixture = start_runner(ctx, fresh_session_on_enter: ["Agent Review"])
    first = await_turn(ctx, 1)
    File.touch!(Path.join(ctx.workspace, "hold-initialize"))
    move(fixture, "Agent Review")
    await(fn -> File.exists?(Path.join(ctx.workspace, "initialize-after-#{first["pid"]}")) end)

    set_issue(ctx, "Retest")
    File.rm!(Path.join(ctx.workspace, "hold-initialize"))

    second = await_turn(ctx, 2)
    assert second["prior_exit"]
    assert second["prompt"] =~ "is Retest"
    refute second["prompt"] =~ "Agent Review"
  end

  test "draining during replacement startup closes the unused native session", ctx do
    fixture = start_runner(ctx, fresh_session_on_enter: ["Agent Review"])
    first = await_turn(ctx, 1)
    File.touch!(Path.join(ctx.workspace, "hold-initialize"))
    move(fixture, "Agent Review")
    receipt = Path.join(ctx.workspace, "initialize-after-#{first["pid"]}")
    await(fn -> File.exists?(receipt) end)
    replacement_pid = File.read!(receipt)

    assert {:ok, %{draining: true}} = GenServer.call(fixture.server, {:agent_control, :drain, false})
    File.rm!(Path.join(ctx.workspace, "hold-initialize"))
    await(fn -> File.exists?(Path.join(ctx.workspace, "exited-#{replacement_pid}")) end)
    await(fn -> running(fixture) == nil end)
    assert length(turns(ctx)) == 1
  end

  test "a terminal observation during shutdown still removes the workspace while drained", ctx do
    fixture = start_runner(ctx, fresh_session_on_enter: ["Agent Review"])
    first = await_turn(ctx, 1)
    hold = Path.join(ctx.workspace, "hold-exit-#{first["pid"]}")
    File.touch!(hold)
    move(fixture, "Agent Review")
    await(fn -> File.exists?(Path.join(ctx.workspace, "stopped-#{first["pid"]}")) end)
    assert {:ok, %{draining: true}} = GenServer.call(fixture.server, {:agent_control, :drain, false})
    move(fixture, "Done")
    File.rm!(hold)

    await(fn -> not File.exists?(ctx.workspace) and not claimed?(fixture) end)
  end

  test "a confirmed startup failure before registration releases transition cleanup", ctx do
    File.touch!(Path.join(ctx.workspace, "hold-initialize"))
    fixture = start_runner(ctx, fresh_session_on_enter: ["Agent Review"], max_retry_backoff_ms: 200)
    await(fn -> File.exists?(Path.join(ctx.workspace, "initialize-first")) end)
    move(fixture, "Agent Review")
    assert running(fixture).session_starting
    assert running(fixture).tracker_stop.reason == :fresh_session
    File.touch!(Path.join(ctx.workspace, "fail-initialize"))
    File.rm!(Path.join(ctx.workspace, "hold-initialize"))

    first = await_turn(ctx, 1)
    assert first["prompt"] =~ "Agent Review"
    assert first["pid"] != String.to_integer(File.read!(Path.join(ctx.workspace, "initialize-first")))
  end

  test "scheduler restart preserves an unconfirmed captured stop", ctx do
    fixture = start_runner(ctx, fresh_session_on_enter: ["Agent Review"])
    first = await_turn(ctx, 1)
    hold = Path.join(ctx.workspace, "hold-exit-#{first["pid"]}")
    File.touch!(hold)
    move(fixture, "Agent Review")
    await(fn -> match?(%{ref: nil}, running(fixture)) end)

    scheduler = Process.whereis(fixture.server)
    Process.exit(scheduler, :kill)
    await(fn -> is_pid(Process.whereis(fixture.server)) and Process.whereis(fixture.server) != scheduler end)
    assert claimed?(fixture)
    assert length(turns(ctx)) == 1

    File.rm!(hold)
    second = await_turn(ctx, 2)
    assert second["prior_exit"]
    assert second["thread"] != first["thread"]
  end

  defp start_runner(ctx, overrides \\ []) do
    config = Keyword.merge(ctx.config, overrides)
    write_workflow_file!(Workflow.workflow_file_path(), config)
    tasks = Module.concat(__MODULE__, :Tasks)
    runtime = Module.concat(__MODULE__, :Runtime)
    runtime_opts = [name: runtime, orchestrator_name: __MODULE__, task_supervisor_name: tasks]
    start_supervised!({AgentRuntimeSupervisor, runtime_opts})
    fixture = Map.merge(ctx, %{server: __MODULE__, config: config})
    move(fixture, ctx.issue.state)
    fixture
  end

  defp move(fixture, status) do
    set_issue(fixture, status)
    send(fixture.server, :run_poll_cycle)
    :sys.get_state(fixture.server)
  end

  defp set_issue(ctx, status) do
    Application.put_env(:symphony_elixir, :memory_tracker_issues, [%{ctx.issue | state: status}])
  end

  defp running(ctx), do: :sys.get_state(ctx.server).running[ctx.issue.id]
  defp claimed?(ctx), do: MapSet.member?(:sys.get_state(ctx.server).claimed, ctx.issue.id)

  defp await_turn(ctx, count) do
    await(fn -> Enum.at(turns(ctx), count - 1) end)
  end

  defp turns(ctx) do
    case File.read(Path.join(ctx.workspace, "turns")) do
      {:ok, trace} -> for line <- String.split(trace, "\n", trim: true), {:ok, turn} = Jason.decode(line), do: turn
      {:error, :enoent} -> []
    end
  end

  defp await(check, remaining \\ 500)
  defp await(_check, 0), do: flunk("session lifecycle did not advance")

  defp await(check, remaining) do
    case check.() do
      value when value not in [nil, false] ->
        value

      _ ->
        Process.sleep(20)
        await(check, remaining - 1)
    end
  end

  defp write_codex_fixture(path) do
    File.write!(path, """
    #!/usr/bin/env node
    const fs = require('node:fs');
    const readline = require('node:readline');
    const pid = process.pid;
    const thread = 'thread-' + pid;
    const emit = message => process.stdout.write(JSON.stringify(message) + '\\n');
    const input = readline.createInterface({ input: process.stdin });
    let turn = 0;
    let turnTimer;
    input.on('line', line => {
      const request = JSON.parse(line);
      switch (request.method) {
        case 'initialize': {
          if (!fs.existsSync('hold-initialize')) {
            emit({ id: request.id, result: {} });
            break;
          }
          const last = fs.existsSync('turns') ? JSON.parse(fs.readFileSync('turns', 'utf8').trim().split('\\n').at(-1)) : null;
          fs.writeFileSync(last ? 'initialize-after-' + last.pid : 'initialize-first', String(pid));
          const timer = setInterval(() => {
            if (fs.existsSync('hold-initialize')) return;
            clearInterval(timer);
            if (fs.existsSync('fail-initialize')) {
              fs.unlinkSync('fail-initialize');
              emit({ id: request.id, error: { code: -32603, message: 'fixture startup failure' } });
            } else {
              emit({ id: request.id, result: {} });
            }
          }, 10);
          break;
        }
        case 'thread/start':
          emit({ id: request.id, result: { thread: { id: thread } } });
          break;
        case 'turn/start': {
          const prompt = request.params.input[0].text;
          const prior = fs.existsSync('turns') ? JSON.parse(fs.readFileSync('turns', 'utf8').trim().split('\\n').at(-1)) : null;
          const prior_exit = prior !== null && fs.existsSync('exited-' + prior.pid);
          fs.appendFileSync('turns', JSON.stringify({ pid, thread, turn: ++turn, cwd: process.cwd(), prompt, prior_exit }) + '\\n');
          emit({ id: request.id, result: { turn: { id: 'turn-' + turn } } });
          turnTimer = setInterval(() => {
            const finish = 'finish-' + pid;
            if (!fs.existsSync(finish)) return;
            fs.unlinkSync(finish);
            clearInterval(turnTimer);
            emit({ method: 'turn/completed' });
          }, 10);
          break;
        }
      }
    });
    process.on('exit', () => fs.writeFileSync('exited-' + pid, ''));
    input.on('close', () => {
      clearInterval(turnTimer);
      fs.writeFileSync('stopped-' + pid, '');
      const started = Date.now();
      const hold = setInterval(() => {
        if (Date.now() - started >= 15000 || !fs.existsSync('hold-exit-' + pid)) clearInterval(hold);
      }, 10);
    });
    """)

    File.chmod!(path, 0o755)
  end
end
