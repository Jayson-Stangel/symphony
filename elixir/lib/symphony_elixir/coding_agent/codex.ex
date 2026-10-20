defmodule SymphonyElixir.CodingAgent.Codex do
  @moduledoc false
  @behaviour SymphonyElixir.CodingAgent

  alias SymphonyElixir.{AgentPause, Codex.AppServer}

  @pause_kinds %{
    "tool_outcome_uncertain" => :tool_outcome_uncertain,
    "protocol_error" => :protocol_error,
    "policy_blocked" => :policy_blocked,
    "auth_required" => :auth_required,
    "quota_exhausted" => :quota_exhausted
  }

  @impl true
  def start_session(workspace, opts), do: AppServer.start_session(workspace, opts)

  @impl true
  def run_turn(session, prompt, issue, opts) do
    profile = AgentPause.codex_profile()

    with :ok <- check_turn(profile),
         {:ok, result} <- AppServer.run_turn(session, prompt, issue, opts),
         :ok <- AgentPause.complete_probe(profile) do
      {:ok, result}
    end
  end

  defp check_turn(profile) do
    case AgentPause.current() do
      {:paused, %{"kind" => kind}} ->
        {:error, {:agent_pause, Map.get(@pause_kinds, kind, :policy_blocked), %{profile: profile}}}

      _ ->
        case AgentPause.begin_turn(profile) do
          :ok -> :ok
          _ -> {:error, {:agent_pause, :policy_blocked, %{profile: profile}}}
        end
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
