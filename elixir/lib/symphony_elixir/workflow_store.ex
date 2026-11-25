defmodule SymphonyElixir.WorkflowStore do
  @moduledoc """
  Caches the last known good workflow and reloads it when `WORKFLOW.md` changes.
  """

  use GenServer
  require Logger

  alias SymphonyElixir.Config
  alias SymphonyElixir.Config.Schema
  alias SymphonyElixir.Linear.Client, as: LinearClient
  alias SymphonyElixir.{PromptBuilder, RemoteWorkflow, Workflow}
  alias SymphonyElixirWeb.ObservabilityPubSub

  @poll_interval_ms 1_000
  @remote_poll_interval_ms 30_000
  @remote_validation_errors [
    :linear_project_mismatch,
    :linear_endpoint_mismatch,
    :linear_credential_mismatch,
    :remote_codex_wrapper_unsupported
  ]

  defmodule State do
    @moduledoc false

    @type t :: %__MODULE__{}

    defstruct [
      :path,
      :stamp,
      :workflow,
      :settings,
      :accepted_at,
      :checked_at,
      :source,
      :project_details,
      :commit,
      :source_checked_at,
      :source_error,
      :source_block,
      :fetch,
      status: "loaded"
    ]
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

  @doc false
  @spec prepare_remote(RemoteWorkflow.source(), boolean(), (RemoteWorkflow.source() -> {:ok, RemoteWorkflow.candidate()} | {:error, atom()})) ::
          {:ok, State.t()} | {:error, atom()}
  def prepare_remote(source, retain?, fetch \\ &RemoteWorkflow.fetch/1) do
    path = RemoteWorkflow.accepted_path()
    source = if retain?, do: Map.put(source, :scratch_root, Path.dirname(path)), else: source

    with true <- RemoteWorkflow.valid_source?(source),
         {:ok, candidate} <- fetch.(source),
         source = %{source | branch: candidate.branch},
         {:ok, state} <- accept_remote(path, source, candidate, timestamp(), retain?) do
      {:ok, state}
    else
      false -> {:error, :invalid_registration}
      {:error, reason} -> {:error, reason}
    end
  end

  @doc false
  @spec admission_block() :: String.t() | nil
  def admission_block do
    if RemoteWorkflow.source_from_environment() do
      if Process.whereis(__MODULE__), do: GenServer.call(__MODULE__, :admission_block), else: "workflow_source_unavailable"
    end
  end

  @impl true
  def init(_opts) do
    case take_prepared_remote() do
      %State{} = state -> start_remote_poll(state)
      nil -> load_initial()
    end
  end

  defp take_prepared_remote do
    prepared = Application.get_env(:symphony_elixir, :prepared_remote_workflow)
    Application.delete_env(:symphony_elixir, :prepared_remote_workflow)
    prepared
  end

  defp load_initial do
    case RemoteWorkflow.source_from_environment() do
      nil ->
        load_local_initial()

      _source ->
        # The container entrypoint prepares and qualifies the initial workflow.
        # Restart the listener as a unit if that boot snapshot has been lost.
        {:stop, :missing_remote_boot_workflow}
    end
  end

  defp start_remote_poll(state) do
    schedule_poll(@remote_poll_interval_ms)
    {:ok, state}
  end

  defp load_local_initial do
    case load_state(Workflow.workflow_file_path()) do
      {:ok, state} ->
        schedule_poll(@poll_interval_ms)
        {:ok, state}

      {:error, reason} ->
        {:stop, reason}
    end
  end

  @impl true
  def handle_call(:current, _from, %State{source: %{}} = state), do: {:reply, {:ok, state.workflow}, state}

  def handle_call(:current, _from, %State{} = state) do
    case reload_state(state) do
      {:ok, new_state} ->
        {:reply, {:ok, new_state.workflow}, new_state}

      {:error, _reason, new_state} ->
        {:reply, {:ok, new_state.workflow}, new_state}
    end
  end

  def handle_call(:force_reload, _from, %State{source: %{}} = state) do
    result = if state.source_block, do: {:error, state.source_block}, else: :ok
    {:reply, result, state}
  end

  def handle_call(:force_reload, _from, %State{} = state) do
    case reload_state(state) do
      {:ok, new_state} ->
        {:reply, :ok, new_state}

      {:error, reason, new_state} ->
        {:reply, {:error, reason}, new_state}
    end
  end

  def handle_call(:settings, _from, %State{source: %{}} = state), do: {:reply, {:ok, state.settings}, state}

  def handle_call(:settings, _from, %State{} = state) do
    case reload_state(state) do
      {:ok, new_state} ->
        {:reply, {:ok, new_state.settings}, new_state}

      {:error, _reason, new_state} ->
        {:reply, {:ok, new_state.settings}, new_state}
    end
  end

  def handle_call(:admission_block, _from, %State{} = state) do
    reason = if state.source_block, do: "workflow_source_rejected"
    {:reply, reason, state}
  end

  def handle_call(:status, _from, %State{source: %{} = source} = state) do
    status =
      state
      |> status_details()
      |> Map.merge(%{
        branch: source.branch,
        project: source.project,
        project_details: state.project_details,
        commit: state.commit,
        github_url: RemoteWorkflow.github_workflow_url(source.repository, state.commit),
        last_successful_source_check_at: state.source_checked_at,
        source_error: state.source_error,
        source_block: state.source_block
      })

    {:reply, status, state}
  end

  def handle_call(:status, _from, %State{} = state) do
    new_state =
      case reload_state(state) do
        {:ok, reloaded} -> reloaded
        {:error, _reason, reloaded} -> reloaded
      end

    {:reply, status_details(new_state), new_state}
  end

  defp status_details(state) do
    %{sha256: state.stamp, status: state.status, accepted_at: state.accepted_at, checked_at: state.checked_at}
  end

  @impl true
  def handle_info(:poll, %State{source: %{} = source} = state) do
    case File.read(Path.join(Config.agent_state_root(), "agent-draining")) do
      {:ok, "migration:" <> _} ->
        # Keep the reviewed startup commit stable through the admission decision.
        schedule_poll(@remote_poll_interval_ms)
        {:noreply, state}

      _ ->
        parent = self()

        {pid, ref} =
          spawn_monitor(fn ->
            send(parent, {self(), check_remote(state.path, source, state.commit)})
          end)

        {:noreply, %{state | fetch: {pid, ref}}}
    end
  end

  def handle_info({pid, outcome}, %State{fetch: {pid, ref}} = state) do
    Process.demonitor(ref, [:flush])
    finish_remote_poll(state, outcome)
  end

  def handle_info({:DOWN, ref, :process, pid, _reason}, %State{fetch: {pid, ref}} = state) do
    finish_remote_poll(state, {:error, :unreachable})
  end

  def handle_info(:poll, %State{} = state) do
    schedule_poll(@poll_interval_ms)

    case reload_state(state) do
      {:ok, new_state} -> {:noreply, new_state}
      {:error, _reason, new_state} -> {:noreply, new_state}
    end
  end

  defp schedule_poll(interval) do
    Process.send_after(self(), :poll, interval)
  end

  defp check_remote(path, source, commit) do
    case RemoteWorkflow.fetch(source, commit) do
      {:ok, %{content: content} = candidate} ->
        with {:ok, accepted} <- accept_remote(path, source, candidate, timestamp(), false) do
          {:ok, accepted, content}
        end

      outcome ->
        outcome
    end
  end

  defp finish_remote_poll(state, outcome) do
    schedule_poll(@remote_poll_interval_ms)
    updated = apply_remote_result(%{state | fetch: nil}, outcome, timestamp())
    :ok = ObservabilityPubSub.broadcast_update()
    {:noreply, updated}
  end

  defp apply_remote_result(state, {:ok, :unchanged}, time) do
    %{state | status: "loaded", checked_at: time, source_checked_at: time, source_error: nil, source_block: nil}
  end

  defp apply_remote_result(state, {:ok, accepted, content}, time) do
    case retain_remote(state.path, content, true) do
      :ok -> %{accepted | project_details: state.project_details}
      {:error, reason} -> reject_remote(%{state | source_checked_at: time}, reason, time)
    end
  end

  defp apply_remote_result(state, {:error, :unreachable}, time) do
    %{state | status: if(state.source_block, do: "rejected", else: "stale"), checked_at: time, source_error: :unreachable}
  end

  defp apply_remote_result(state, {:error, reason}, time) do
    reject_remote(%{state | source_checked_at: time}, reason, time)
  end

  defp reject_remote(state, reason, time) do
    if state.source_block != reason, do: Logger.warning("Remote workflow rejected reason=#{reason} branch=#{state.source.branch}")
    %{state | status: "rejected", checked_at: time, source_error: reason, source_block: reason}
  end

  defp accept_remote(path, source, candidate, time, retain?) do
    stamp = hash_content(candidate.content)

    with {:ok, state} <- load_content(path, candidate.content, stamp, time, source),
         :ok <- retain_remote(path, candidate.content, retain?) do
      {:ok, %{state | source: source, commit: candidate.commit, source_checked_at: time}}
    end
  end

  defp retain_remote(_path, _content, false), do: :ok

  defp retain_remote(path, content, true) do
    temporary = path <> "." <> Base.url_encode64(:crypto.strong_rand_bytes(18), padding: false)
    directory = Path.dirname(path)

    with :ok <- File.mkdir_p(directory),
         {:ok, %File.Stat{type: :directory}} <- File.lstat(directory),
         :ok <- File.write(temporary, content, [:exclusive, :sync]),
         :ok <- File.rename(temporary, path) do
      :ok
    else
      _ ->
        File.rm(temporary)
        {:error, :workflow_storage_unavailable}
    end
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

  defp load_content(path, content, stamp, checked_at, source \\ nil) do
    with {:ok, workflow} <- Workflow.parse(content),
         {:ok, settings} <- Schema.parse(workflow.config),
         :ok <- validate_source_settings(source, settings),
         :ok <- validate_source_prompt(source, workflow.prompt_template),
         :ok <- Config.validate_settings(settings) do
      if is_nil(source) and settings.execution.mode == "container" do
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
    else
      {:error, reason} when is_nil(source) ->
        {:error, reason}

      {:error, reason} when reason in @remote_validation_errors ->
        {:error, reason}

      {:error, _reason} ->
        {:error, :invalid_workflow}
    end
  end

  defp validate_source_settings(nil, _settings), do: :ok

  defp validate_source_settings(%{project: project}, settings) do
    tracker = settings.tracker

    cond do
      tracker.kind != "linear" or not LinearClient.same_project_slug?(tracker.project_slug, project) ->
        {:error, :linear_project_mismatch}

      tracker.endpoint != "https://api.linear.app/graphql" ->
        {:error, :linear_endpoint_mismatch}

      tracker.provider["api_key"] not in [nil, "$LINEAR_API_KEY"] ->
        {:error, :linear_credential_mismatch}

      Config.coding_agent() == :codex ->
        validate_codex_command(settings.codex.command)

      true ->
        :ok
    end
  end

  defp validate_source_prompt(nil, _prompt), do: :ok

  defp validate_source_prompt(_source, prompt), do: PromptBuilder.validate_template(prompt)

  defp validate_codex_command(command) do
    with {:ok, tokens} <- tokenize_codex_command(command) do
      executable = Path.basename(List.first(tokens, ""))
      if executable == "bash", do: {:error, :remote_codex_wrapper_unsupported}, else: :ok
    end
  end

  defp tokenize_codex_command(command) do
    {:ok, OptionParser.split(command)}
  rescue
    RuntimeError -> {:error, :invalid_workflow}
  end

  defp hash_content(content), do: :crypto.hash(:sha256, content) |> Base.encode16(case: :lower)

  defp read_workflow(path) do
    case File.read(path) do
      {:ok, content} -> {:ok, content, hash_content(content)}
      {:error, reason} -> {:error, {:missing_workflow_file, path, reason}}
    end
  end

  defp timestamp, do: DateTime.utc_now() |> DateTime.to_iso8601()

  defp log_reload_error(path, reason) do
    Logger.error("Failed to reload workflow path=#{path} reason=#{inspect(reason)}; keeping last known good configuration")
  end
end
