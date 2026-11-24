defmodule SymphonyElixir.CLI do
  @moduledoc """
  Escript entrypoint for running Symphony with an explicit WORKFLOW.md path.
  """

  alias SymphonyElixir.Codex.{ConfigSnapshot, SandboxReadiness}
  alias SymphonyElixir.Linear.Client
  alias SymphonyElixir.{LogFile, RemoteWorkflow, WorkflowStore}

  @acknowledgement_switch :i_understand_that_this_will_be_running_without_the_usual_guardrails
  @switches [{@acknowledgement_switch, :boolean}, logs_root: :string, port: :integer, agent: [:string, :keep]]

  @type ensure_started_result :: {:ok, [atom()]} | {:error, term()}
  @type deps :: %{
          optional(:set_coding_agent) => (:codex | :claude -> :ok),
          optional(:check_sandbox) => (-> :ok | {:error, String.t()}),
          file_regular?: (String.t() -> boolean()),
          set_workflow_file_path: (String.t() -> :ok | {:error, term()}),
          set_logs_root: (String.t() -> :ok | {:error, term()}),
          set_server_port_override: (non_neg_integer() | nil -> :ok | {:error, term()}),
          ensure_all_started: (-> ensure_started_result())
        }

  @spec main([String.t()]) :: no_return()
  def main(args) do
    main(args, fn -> Application.ensure_all_started(:symphony_elixir) end)
  end

  @doc false
  @spec main([String.t()], (-> ensure_started_result())) :: no_return()
  def main(["--codex-config-snapshot"], _ensure_all_started) do
    case ConfigSnapshot.run() do
      {:ok, value} ->
        IO.puts(Jason.encode!(value))
        System.halt(0)

      {:error, code} ->
        IO.puts(Jason.encode!(%{schema: 1, mode: "codex-config-snapshot", error: code}))
        System.halt(1)
    end
  end

  def main(["--remote-workflow-probe", agent], _ensure_all_started) when agent in ["codex", "claude"] do
    :ok = set_coding_agent(if(agent == "claude", do: :claude, else: :codex))

    case RemoteWorkflow.source_from_environment() do
      nil ->
        stop_remote(:invalid_registration)

      source ->
        with {:ok, state} <- WorkflowStore.prepare_remote(source, false),
             :ok <- Client.verify_project(state.settings.tracker) do
          IO.puts(state.source.branch)
          System.halt(0)
        else
          {:error, reason} -> stop_remote(reason)
        end
    end
  end

  def main(["--check-codex-remote-sandbox"], _ensure_all_started) do
    with %{} = source <- RemoteWorkflow.source_from_environment(),
         {:ok, state} <- WorkflowStore.prepare_remote(source, true),
         :ok <- Client.verify_project(state.settings.tracker),
         :ok <- SymphonyElixir.Workflow.set_workflow_file_path(state.path),
         :ok <- SandboxReadiness.check(credential_free: true) do
      IO.puts("Codex sandbox protected-command checks passed.")
      System.halt(0)
    else
      nil ->
        stop_remote(:invalid_registration)

      {:error, reason} when is_atom(reason) ->
        stop_remote(reason)

      {:error, code} ->
        IO.puts(:stderr, "Codex sandbox not ready: #{code}")
        System.halt(1)
    end
  end

  def main(args, ensure_all_started) do
    if match?(["--check-codex-sandbox", _], args) do
      [_, workflow] = args
      SymphonyElixir.Workflow.set_workflow_file_path(Path.expand(workflow))

      case SandboxReadiness.check(credential_free: true) do
        :ok ->
          IO.puts("Codex sandbox protected-command checks passed.")

        {:error, code} ->
          IO.puts(:stderr, "Codex sandbox not ready: #{code}")
          System.halt(1)
      end

      System.halt(0)
    end

    case evaluate(args, runtime_deps(ensure_all_started)) do
      :ok ->
        wait_for_shutdown()

      {:error, message} ->
        IO.puts(:stderr, message)
        System.halt(1)
    end
  end

  @spec evaluate([String.t()], deps()) :: :ok | {:error, String.t()}
  def evaluate(args, deps \\ runtime_deps()) do
    case OptionParser.parse(args, strict: @switches) do
      {opts, [], []} ->
        with :ok <- require_guardrails_acknowledgement(opts),
             :ok <- maybe_set_coding_agent(opts, deps),
             :ok <- maybe_set_logs_root(opts, deps),
             :ok <- maybe_set_server_port(opts, deps) do
          run(Path.expand("WORKFLOW.md"), deps)
        end

      {opts, [workflow_path], []} ->
        with :ok <- require_guardrails_acknowledgement(opts),
             :ok <- maybe_set_coding_agent(opts, deps),
             :ok <- maybe_set_logs_root(opts, deps),
             :ok <- maybe_set_server_port(opts, deps) do
          run(workflow_path, deps)
        end

      _ ->
        {:error, usage_message()}
    end
  end

  @spec run(String.t(), deps()) :: :ok | {:error, String.t()}
  def run(workflow_path, deps) do
    expanded_path = Path.expand(workflow_path)

    with :ok <- prepare_remote_listener(expanded_path) do
      run_local(expanded_path, deps)
    end
  end

  defp run_local(expanded_path, deps) do
    if deps.file_regular?.(expanded_path) do
      :ok = deps.set_workflow_file_path.(expanded_path)

      with :ok <- Map.get(deps, :check_sandbox, fn -> :ok end).() do
        start_application(expanded_path, deps)
      end
    else
      {:error, "Workflow file not found: #{expanded_path}"}
    end
  end

  defp prepare_remote_listener(path) do
    case RemoteWorkflow.source_from_environment() do
      nil ->
        :ok

      source ->
        with true <- path == RemoteWorkflow.accepted_path() and source.branch != "",
             {:ok, state} <- WorkflowStore.prepare_remote(source, true) do
          Application.put_env(:symphony_elixir, :prepared_remote_workflow, state)
          :ok
        else
          false -> {:error, remote_message(:invalid_registration)}
          {:error, reason} -> {:error, remote_message(reason)}
        end
    end
  end

  @spec stop_remote(atom()) :: no_return()
  defp stop_remote(reason) do
    IO.puts(:stderr, remote_message(reason))
    System.halt(1)
  end

  defp remote_message(reason) do
    "Remote workflow unavailable (#{reason}). Check the registered repository, watched branch, root WORKFLOW.md, " <>
      "Linear project and project credentials, then retry."
  end

  defp start_application(path, deps) do
    case deps.ensure_all_started.() do
      {:ok, _started_apps} -> :ok
      {:error, reason} -> {:error, "Failed to start Symphony with workflow #{path}: #{inspect(reason)}"}
    end
  end

  @spec usage_message() :: String.t()
  defp usage_message do
    "Usage: symphony [--agent codex|claude] [--logs-root <path>] [--port <port>] [path-to-WORKFLOW.md]"
  end

  @spec runtime_deps() :: deps()
  defp runtime_deps(ensure_all_started \\ fn -> Application.ensure_all_started(:symphony_elixir) end) do
    %{
      file_regular?: &File.regular?/1,
      set_coding_agent: &set_coding_agent/1,
      set_workflow_file_path: &SymphonyElixir.Workflow.set_workflow_file_path/1,
      set_logs_root: &set_logs_root/1,
      set_server_port_override: &set_server_port_override/1,
      ensure_all_started: ensure_all_started,
      check_sandbox: &check_sandbox/0
    }
  end

  defp check_sandbox do
    if SymphonyElixir.Config.coding_agent() == :codex do
      case SandboxReadiness.check() do
        :ok -> :ok
        {:error, code} -> {:error, "Codex sandbox not ready: #{code}"}
      end
    else
      :ok
    end
  end

  defp maybe_set_coding_agent(opts, deps) do
    provider =
      case Keyword.get_values(opts, :agent) do
        [] -> :codex
        ["codex"] -> :codex
        ["claude"] -> :claude
        _ -> :invalid
      end

    if provider == :invalid do
      {:error, usage_message()}
    else
      setter = Map.get(deps, :set_coding_agent, fn _provider -> :ok end)
      setter.(provider)
    end
  end

  defp set_coding_agent(provider) do
    Application.put_env(:symphony_elixir, :coding_agent, provider)
    :ok
  end

  defp maybe_set_logs_root(opts, deps) do
    case Keyword.get_values(opts, :logs_root) do
      [] ->
        :ok

      values ->
        logs_root = values |> List.last() |> String.trim()

        if logs_root == "" do
          {:error, usage_message()}
        else
          :ok = deps.set_logs_root.(Path.expand(logs_root))
        end
    end
  end

  defp require_guardrails_acknowledgement(opts) do
    if Keyword.get(opts, @acknowledgement_switch, false) do
      :ok
    else
      {:error, acknowledgement_banner()}
    end
  end

  @spec acknowledgement_banner() :: String.t()
  defp acknowledgement_banner do
    lines = [
      "This Symphony implementation is a low key engineering preview.",
      "The coding agent will run without any guardrails.",
      "SymphonyElixir is not a supported product and is presented as-is.",
      "To proceed, start with `--i-understand-that-this-will-be-running-without-the-usual-guardrails` CLI argument"
    ]

    width = Enum.max(Enum.map(lines, &String.length/1))
    border = String.duplicate("─", width + 2)
    top = "╭" <> border <> "╮"
    bottom = "╰" <> border <> "╯"
    spacer = "│ " <> String.duplicate(" ", width) <> " │"

    content =
      [
        top,
        spacer
        | Enum.map(lines, fn line ->
            "│ " <> String.pad_trailing(line, width) <> " │"
          end)
      ] ++ [spacer, bottom]

    [
      IO.ANSI.red(),
      IO.ANSI.bright(),
      Enum.join(content, "\n"),
      IO.ANSI.reset()
    ]
    |> IO.iodata_to_binary()
  end

  defp set_logs_root(logs_root) do
    Application.put_env(:symphony_elixir, :log_file, LogFile.default_log_file(logs_root))
    :ok
  end

  defp maybe_set_server_port(opts, deps) do
    case Keyword.get_values(opts, :port) do
      [] ->
        :ok

      values ->
        port = List.last(values)

        if is_integer(port) and port >= 0 do
          :ok = deps.set_server_port_override.(port)
        else
          {:error, usage_message()}
        end
    end
  end

  defp set_server_port_override(port) when is_integer(port) and port >= 0 do
    Application.put_env(:symphony_elixir, :server_port_override, port)
    :ok
  end

  @spec wait_for_shutdown() :: no_return()
  defp wait_for_shutdown do
    case Process.whereis(SymphonyElixir.Supervisor) do
      nil ->
        IO.puts(:stderr, "Symphony supervisor is not running")
        System.halt(1)

      pid ->
        ref = Process.monitor(pid)

        receive do
          {:DOWN, ^ref, :process, ^pid, reason} ->
            case reason do
              :normal -> System.halt(0)
              _ -> System.halt(1)
            end
        end
    end
  end
end
