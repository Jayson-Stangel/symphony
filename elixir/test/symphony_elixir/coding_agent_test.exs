defmodule SymphonyElixir.CodingAgentTest do
  use ExUnit.Case, async: true

  alias SymphonyElixir.CodingAgent

  defmodule Probe do
    @behaviour CodingAgent

    @impl true
    def start_session(workspace, opts) do
      if Keyword.get(opts, :fail_start) do
        {:error, :startup_failed}
      else
        state = %{workspace: workspace, turns: [], interrupted: false, stopped: false}
        {:ok, agent} = Agent.start_link(fn -> state end)
        {:ok, agent}
      end
    end

    @impl true
    def run_turn(agent, prompt, issue, opts) do
      Agent.update(agent, &%{&1 | turns: &1.turns ++ [{prompt, issue, opts}]})
      Keyword.get(opts, :outcome, {:ok, %{session_id: "probe-session"}})
    end

    @impl true
    def interrupt(agent, _reason), do: Agent.update(agent, &%{&1 | interrupted: true})

    @impl true
    def stop_session(agent), do: Agent.update(agent, &%{&1 | stopped: true})
  end

  test "continuations use one captured adapter and native session" do
    assert {:ok, session} = CodingAgent.start_session("/workspace", adapter: Probe)
    on_exit(fn -> if Process.alive?(session.native), do: Agent.stop(session.native) end)
    assert session.adapter == Probe
    assert {:ok, %{session_id: "probe-session"}} = CodingAgent.run_turn(session, "first", %{id: "one"})
    assert {:error, :turn_failed} = CodingAgent.run_turn(session, "second", %{id: "one"}, outcome: {:error, :turn_failed})
    assert :ok = CodingAgent.interrupt(session, :cancelled)
    assert :ok = CodingAgent.interrupt(session, :cancelled)
    assert :ok = CodingAgent.stop_session(session)
    assert :ok = CodingAgent.stop_session(session)
    state = Agent.get(session.native, & &1)
    assert state.workspace == "/workspace"
    assert Enum.map(state.turns, &elem(&1, 0)) == ["first", "second"]
    assert state.interrupted
    assert state.stopped
  end

  test "startup failure does not create a session wrapper" do
    assert {:error, :startup_failed} = CodingAgent.start_session("/workspace", adapter: Probe, fail_start: true)
  end

  test "Codex interruption preserves its existing idempotent port shutdown" do
    port = Port.open({:spawn_executable, System.find_executable("cat") |> String.to_charlist()}, [:binary])
    native = %{port: port}
    assert :ok = CodingAgent.Codex.interrupt(native, :cancelled)
    assert :ok = CodingAgent.Codex.interrupt(native, :cancelled)
    assert :ok = CodingAgent.Codex.stop_session(native)
    assert Port.info(port) == nil
  end
end
