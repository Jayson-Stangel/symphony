defmodule SymphonyElixir.WorkflowStore do
  @moduledoc """
  Caches the last known good workflow and reloads it when `WORKFLOW.md` changes.
  """

  use GenServer
  require Logger

  alias SymphonyElixir.Config
  alias SymphonyElixir.Config.Schema
  alias SymphonyElixir.Workflow

  @poll_interval_ms 1_000

  defmodule State do
    @moduledoc false

    defstruct [:path, :stamp, :workflow, :settings, :accepted_at, :checked_at, status: "loaded"]
  end

  @spec start_link(keyword()) :: GenServer.on_start()
  def start_link(opts \\ []) do
    GenServer.start_link(__MODULE__, opts, name: __MODULE__)
  end

  @spec current() :: {:ok, Workflow.loaded_workflow()} | {:error, term()}
  def current do
    case Process.whereis(__MODULE__) do
      pid when is_pid(pid) ->
        GenServer.call(__MODULE__, :current)

      _ ->
        Workflow.load()
    end
  end

  @spec settings() :: {:ok, Schema.t()} | {:error, term()}
  def settings do
    case Process.whereis(__MODULE__) do
      pid when is_pid(pid) ->
        GenServer.call(__MODULE__, :settings)

      _ ->
        case load_state(Workflow.workflow_file_path()) do
          {:ok, %State{settings: settings}} -> {:ok, settings}
          {:error, reason} -> {:error, reason}
        end
    end
  end

  @doc "Returns only accepted-byte identity and safe reload status; never workflow contents or errors."
  @spec status() :: map()
  def status do
    case Process.whereis(__MODULE__) do
      pid when is_pid(pid) -> GenServer.call(__MODULE__, :status)
      _ -> %{sha256: nil, status: "unavailable", accepted_at: nil, checked_at: nil}
    end
  end

  @spec force_reload() :: :ok | {:error, term()}
  def force_reload do
    case Process.whereis(__MODULE__) do
      pid when is_pid(pid) ->
        GenServer.call(__MODULE__, :force_reload)

      _ ->
        case load_state(Workflow.workflow_file_path()) do
          {:ok, _state} -> :ok
          {:error, reason} -> {:error, reason}
        end
    end
  end

  @impl true
  def init(_opts) do
    case load_state(Workflow.workflow_file_path()) do
      {:ok, state} ->
        schedule_poll()
        {:ok, state}

      {:error, reason} ->
        {:stop, reason}
    end
  end

  @impl true
  def handle_call(:current, _from, %State{} = state) do
    case reload_state(state) do
      {:ok, new_state} ->
        {:reply, {:ok, new_state.workflow}, new_state}

      {:error, _reason, new_state} ->
        {:reply, {:ok, new_state.workflow}, new_state}
    end
  end

  def handle_call(:force_reload, _from, %State{} = state) do
    case reload_state(state) do
      {:ok, new_state} ->
        {:reply, :ok, new_state}

      {:error, reason, new_state} ->
        {:reply, {:error, reason}, new_state}
    end
  end

  def handle_call(:settings, _from, %State{} = state) do
    case reload_state(state) do
      {:ok, new_state} ->
        {:reply, {:ok, new_state.settings}, new_state}

      {:error, _reason, new_state} ->
        {:reply, {:ok, new_state.settings}, new_state}
    end
  end

  def handle_call(:status, _from, %State{} = state) do
    new_state =
      case reload_state(state) do
        {:ok, reloaded} -> reloaded
        {:error, _reason, reloaded} -> reloaded
      end

    status = %{
      sha256: new_state.stamp,
      status: new_state.status,
      accepted_at: new_state.accepted_at,
      checked_at: new_state.checked_at
    }

    {:reply, status, new_state}
  end

  @impl true
  def handle_info(:poll, %State{} = state) do
    schedule_poll()

    case reload_state(state) do
      {:ok, new_state} -> {:noreply, new_state}
      {:error, _reason, new_state} -> {:noreply, new_state}
    end
  end

  defp schedule_poll do
    Process.send_after(self(), :poll, @poll_interval_ms)
  end

  defp reload_state(%State{} = state) do
    path = Workflow.workflow_file_path()
    checked_at = timestamp()

    case read_workflow(path) do
      {:ok, content, stamp} ->
        reload_content(path, content, stamp, state, checked_at)

      {:error, reason} ->
        reject_reload(path, reason, state, checked_at)
    end
  end

  defp reload_content(path, _content, stamp, state, checked_at)
       when path == state.path and stamp == state.stamp do
    {:ok, %{state | status: "loaded", checked_at: checked_at}}
  end

  defp reload_content(path, content, stamp, state, checked_at) do
    case load_content(path, content, stamp, checked_at) do
      {:ok, new_state} -> {:ok, new_state}
      {:error, reason} -> reject_reload(path, reason, state, checked_at)
    end
  end

  defp reject_reload(path, reason, state, checked_at) do
    log_reload_error(path, reason)
    {:error, reason, %{state | status: "rejected", checked_at: checked_at}}
  end

  defp load_state(path) do
    with {:ok, content, stamp} <- read_workflow(path) do
      load_content(path, content, stamp, timestamp())
    end
  end

  defp load_content(path, content, stamp, checked_at) do
    with {:ok, workflow} <- Workflow.parse(content),
         {:ok, settings} <- Schema.parse(workflow.config),
         :ok <- Config.validate_settings(settings) do
      if settings.execution.mode == "container" do
        Logger.info("Container execution bindings: #{inspect(settings.execution)}")
      end

      {:ok,
       %State{
         path: path,
         stamp: stamp,
         workflow: workflow,
         settings: settings,
         accepted_at: checked_at,
         checked_at: checked_at
       }}
    end
  end

  defp read_workflow(path) do
    case File.read(path) do
      {:ok, content} -> {:ok, content, :crypto.hash(:sha256, content) |> Base.encode16(case: :lower)}
      {:error, reason} -> {:error, {:missing_workflow_file, path, reason}}
    end
  end

  defp timestamp, do: DateTime.utc_now() |> DateTime.to_iso8601()

  defp log_reload_error(path, reason) do
    Logger.error("Failed to reload workflow path=#{path} reason=#{inspect(reason)}; keeping last known good configuration")
  end
end
