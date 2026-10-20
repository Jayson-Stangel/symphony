defmodule SymphonyElixir.CodingAgent.Codex do
  @moduledoc false
  @behaviour SymphonyElixir.CodingAgent

  alias SymphonyElixir.Codex.AppServer

  @impl true
  def start_session(workspace, opts), do: AppServer.start_session(workspace, opts)

  @impl true
  def run_turn(session, prompt, issue, opts), do: AppServer.run_turn(session, prompt, issue, opts)

  # Interrupt invalidates this native session. A new attempt starts a new one.
  # Preserve the existing Codex shutdown path rather than adding another protocol.
  @impl true
  def interrupt(session, _reason), do: AppServer.stop_session(session)

  @impl true
  def stop_session(session), do: AppServer.stop_session(session)
end
