defmodule SymphonyElixir.CodingAgent do
  @moduledoc """
  Coding-session lifecycle boundary. The worker owns continuation and tracker policy;
  an adapter owns only its native session. The adapter is captured once per attempt.
  """

  alias SymphonyElixir.CodingAgent.Codex
  alias SymphonyElixir.Config

  @type session :: %{adapter: module(), native: term()}
  @callback start_session(Path.t(), keyword()) :: {:ok, term()} | {:error, term()}
  @callback run_turn(term(), String.t(), map(), keyword()) :: {:ok, map()} | {:error, term()}
  @callback interrupt(term(), term()) :: :ok | {:error, term()}
  @callback stop_session(term()) :: :ok | {:error, term()}

  @spec start_session(Path.t(), keyword()) :: {:ok, session()} | {:error, term()}
  def start_session(workspace, opts \\ []) do
    {override, native_opts} = Keyword.pop(opts, :adapter)
    adapter = override || selected_adapter()

    start_with_adapter(adapter, workspace, native_opts)
  end

  defp selected_adapter do
    case Config.coding_agent() do
      :codex -> Codex
      :claude -> SymphonyElixir.Claude.Adapter
    end
  end

  defp start_with_adapter(adapter, workspace, native_opts) do
    with {:ok, native} <- adapter.start_session(workspace, native_opts) do
      {:ok, %{adapter: adapter, native: native}}
    end
  end

  @spec run_turn(session(), String.t(), map(), keyword()) :: {:ok, map()} | {:error, term()}
  def run_turn(%{adapter: adapter, native: native}, prompt, issue, opts \\ []) do
    adapter.run_turn(native, prompt, issue, opts)
  end

  @spec interrupt(session(), term()) :: :ok | {:error, term()}
  def interrupt(%{adapter: adapter, native: native}, reason) do
    adapter.interrupt(native, reason)
  end

  @spec stop_session(session()) :: :ok | {:error, term()}
  def stop_session(%{adapter: adapter, native: native}) do
    adapter.stop_session(native)
  end
end
