defmodule SymphonyElixir.Config do
  @moduledoc """
  Runtime configuration loaded from `WORKFLOW.md`.
  """

  alias SymphonyElixir.{Config.Schema, Tracker}
  alias SymphonyElixir.{Workflow, WorkflowStore}

  @default_prompt_template """
  You are working on an issue from the configured tracker.

  Identifier: {{ issue.identifier }}
  Title: {{ issue.title }}

  Body:
  {% if issue.description %}
  {{ issue.description }}
  {% else %}
  No description provided.
  {% endif %}
  """

  @type codex_runtime_settings :: %{
          model: String.t() | nil,
          reasoning_effort: String.t() | nil,
          approval_policy: String.t() | map(),
          thread_sandbox: String.t(),
          turn_sandbox_policy: map()
        }

  @spec settings() :: {:ok, Schema.t()} | {:error, term()}
  def settings do
    WorkflowStore.settings()
  end

  @doc "The CLI-selected provider is startup state, independent of workflow reloads."
  @spec coding_agent() :: :codex | :claude
  def coding_agent do
    case Application.get_env(:symphony_elixir, :coding_agent, :codex) do
      provider when provider in [:codex, :claude] -> provider
      _ -> raise ArgumentError, "Unsupported coding agent"
    end
  end

  @spec agent_state_root() :: Path.t()
  def agent_state_root do
    Application.get_env(:symphony_elixir, :agent_state_root, "/var/lib/symphony")
  end

  @spec claude_helper_path() :: Path.t()
  def claude_helper_path, do: "/opt/symphony/claude/helper.mjs"

  @spec claude_node_path() :: Path.t()
  def claude_node_path, do: "/usr/local/bin/node"

  @spec claude_cleanup_environment() :: [{String.t(), String.t() | nil}]
  def claude_cleanup_environment do
    Enum.map(reduced_claude_environment(System.get_env()), fn {key, value} -> {key, if(key == "SYMPHONY_GIT_TOKEN", do: nil, else: value)} end)
  end

  @spec claude_child_environment(map(), [String.t()] | nil) ::
          {:ok, [{String.t(), String.t() | nil}]} | {:error, :invalid_tool_environment}
  def claude_child_environment(environment \\ System.get_env(), secret_names \\ nil) do
    names = String.split(environment["SYMPHONY_TOOL_ENV"] || "", ",", trim: true) |> Enum.map(&String.trim/1)
    reserved = secret_names || Tracker.bind_agent_tools().secret_environment_names

    case Jason.encode(%{names: names, values: Map.take(environment, names), reserved: reserved}) do
      {:ok, envelope} when byte_size(envelope) <= 65_536 ->
        env = reduced_claude_environment(environment) |> Map.new() |> Map.put("SYMPHONY_TOOL_ENVIRONMENT", envelope)
        {:ok, Enum.to_list(env)}

      _ ->
        {:error, :invalid_tool_environment}
    end
  end

  defp reduced_claude_environment(environment) do
    # Port environments inherit by default. Explicitly remove every ambient key.
    removed = Enum.map(environment, fn {key, _} -> {key, nil} end)

    fixed = [
      {"HOME", "/home/claude"},
      {"CLAUDE_CONFIG_DIR", "/home/claude/.claude"},
      {"CLAUDE_CODE_DISABLE_FAST_MODE", "1"},
      {"DISABLE_AUTOUPDATER", "1"},
      {"PATH", "/usr/local/bin:/usr/bin:/bin"},
      {"LANG", "C.UTF-8"},
      {"SYMPHONY_CONTAINERIZED", environment["SYMPHONY_CONTAINERIZED"]},
      {"SYMPHONY_GIT_TOKEN", environment["SYMPHONY_GIT_TOKEN"]}
    ]

    Enum.into(fixed, Map.new(removed)) |> Enum.to_list()
  end

  @spec max_concurrent_agents() :: pos_integer()
  def max_concurrent_agents do
    config = settings!()
    config.agent.max_concurrent_agents
  end

  @spec agent_stall_timeout_ms() :: non_neg_integer()
  def agent_stall_timeout_ms do
    config = settings!()
    if coding_agent() == :claude, do: config.claude.stall_timeout_ms, else: config.codex.stall_timeout_ms
  end

  @spec settings!() :: Schema.t()
  def settings! do
    case settings() do
      {:ok, settings} ->
        settings

      {:error, reason} ->
        raise ArgumentError, message: format_config_error(reason)
    end
  end

  @spec max_concurrent_agents_for_state(term()) :: pos_integer()
  def max_concurrent_agents_for_state(state_name) when is_binary(state_name) do
    config = settings!()

    Map.get(
      config.agent.max_concurrent_agents_by_state,
      Schema.normalize_issue_state(state_name),
      config.agent.max_concurrent_agents
    )
  end

  def max_concurrent_agents_for_state(_state_name), do: max_concurrent_agents()

  @spec codex_turn_sandbox_policy(Path.t() | nil) :: map()
  def codex_turn_sandbox_policy(workspace \\ nil) do
    case Schema.resolve_runtime_turn_sandbox_policy(settings!(), workspace) do
      {:ok, policy} ->
        policy

      {:error, reason} ->
        raise ArgumentError, message: "Invalid codex turn sandbox policy: #{inspect(reason)}"
    end
  end

  @spec workflow_prompt() :: String.t()
  def workflow_prompt do
    case Workflow.current() do
      {:ok, %{prompt_template: prompt}} ->
        if String.trim(prompt) == "", do: @default_prompt_template, else: prompt

      _ ->
        @default_prompt_template
    end
  end

  @spec server_port() :: non_neg_integer() | nil
  def server_port do
    case Application.get_env(:symphony_elixir, :server_port_override) do
      port when is_integer(port) and port >= 0 -> port
      _ -> settings!().server.port
    end
  end

  @doc false
  @spec local_workspace_root() :: Path.t()
  def local_workspace_root do
    workflow_dir = Workflow.workflow_file_path() |> Path.expand() |> Path.dirname()
    Path.expand(settings!().workspace.root, workflow_dir)
  end

  @spec validate!() :: :ok | {:error, term()}
  def validate! do
    WorkflowStore.force_reload()
  end

  @spec codex_runtime_settings(Path.t() | nil, keyword()) ::
          {:ok, codex_runtime_settings()} | {:error, term()}
  def codex_runtime_settings(workspace \\ nil, opts \\ []) do
    with {:ok, settings} <- settings() do
      resolve_codex_runtime_settings(settings, workspace, opts)
    end
  end

  @doc false
  @spec resolve_codex_runtime_settings(Schema.t(), Path.t() | nil, keyword()) ::
          {:ok, codex_runtime_settings()} | {:error, term()}
  def resolve_codex_runtime_settings(settings, workspace, opts) do
    with {:ok, turn_sandbox_policy} <-
           Schema.resolve_runtime_turn_sandbox_policy(settings, workspace, opts) do
      {:ok,
       %{
         model: settings.codex.model,
         reasoning_effort: settings.codex.reasoning_effort,
         approval_policy: settings.codex.approval_policy,
         thread_sandbox: settings.codex.thread_sandbox,
         turn_sandbox_policy: turn_sandbox_policy
       }}
    end
  end

  @doc false
  @spec validate_settings(Schema.t()) :: :ok | {:error, term()}
  def validate_settings(settings) do
    cond do
      is_nil(settings.tracker.kind) -> {:error, :missing_tracker_kind}
      coding_agent() == :claude and settings.worker.ssh_hosts != [] -> {:error, :claude_ssh_unsupported}
      true -> Tracker.validate_config(settings.tracker)
    end
  end

  defp format_config_error(reason) do
    case reason do
      {:invalid_workflow_config, message} ->
        "Invalid WORKFLOW.md config: #{message}"

      {:missing_workflow_file, path, raw_reason} ->
        "Missing WORKFLOW.md at #{path}: #{inspect(raw_reason)}"

      {:workflow_parse_error, raw_reason} ->
        "Failed to parse WORKFLOW.md: #{inspect(raw_reason)}"

      :workflow_front_matter_not_a_map ->
        "Failed to parse WORKFLOW.md: workflow front matter must decode to a map"

      other ->
        "Invalid WORKFLOW.md config: #{inspect(other)}"
    end
  end
end
