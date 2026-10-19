defmodule SymphonyElixir.CodingAgent.Codex do
  @moduledoc false
  @behaviour SymphonyElixir.CodingAgent

  alias SymphonyElixir.{AgentPause, Codex.AppServer}

  @impl true
  def start_session(workspace, opts), do: AppServer.start_session(workspace, opts)

  @impl true
  def run_turn(session, prompt, issue, opts) do
    profile = AgentPause.codex_profile()

    with :ok <- AgentPause.begin_turn(profile),
         {:ok, result} <- AppServer.run_turn(session, prompt, issue, opts),
         :ok <- AgentPause.complete_probe(profile) do
      {:ok, result}
    else
      {:error, :probe_not_available} -> {:error, {:agent_pause, :tool_outcome_uncertain, %{profile: profile}}}
      error -> error
    end
  end

  # Interrupt invalidates this native session. A new attempt starts a new one.
  # Preserve the existing Codex shutdown path rather than adding another protocol.
  @impl true
  def interrupt(session, reason) do
    with :ok <- AppServer.stop_session(session) do
      if reason == :issue_state_change, do: AgentPause.cancel_recheck(AgentPause.codex_profile()), else: :ok
    end
  end

  @impl true
  def stop_session(session), do: AppServer.stop_session(session)
end
