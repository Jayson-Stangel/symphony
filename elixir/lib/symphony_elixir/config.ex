defmodule SymphonyElixir.Config do
  @moduledoc """
  Runtime configuration loaded from `WORKFLOW.md`.
  """

  alias SymphonyElixir.Config.Schema
  alias SymphonyElixir.Workflow

  @default_prompt_template """
  You are working on a Linear issue.

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
          approval_policy: String.t() | map(),
          thread_sandbox: String.t(),
          turn_sandbox_policy: map()
        }

  @spec settings() :: {:ok, Schema.t()} | {:error, term()}
  def settings do
    case Workflow.current() do
      {:ok, %{config: config}} when is_map(config) ->
        Schema.parse(config)

      {:error, reason} ->
        {:error, reason}
    end
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

  def max_concurrent_agents_for_state(_state_name), do: settings!().agent.max_concurrent_agents

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

  @spec validate!() :: :ok | {:error, term()}
  def validate! do
    with {:ok, settings} <- settings() do
      validate_semantics(settings)
    end
  end

  @spec codex_runtime_settings(Path.t() | nil, keyword()) ::
          {:ok, codex_runtime_settings()} | {:error, term()}
  def codex_runtime_settings(workspace \\ nil, opts \\ []) do
    with {:ok, settings} <- settings() do
      with {:ok, turn_sandbox_policy} <-
             Schema.resolve_runtime_turn_sandbox_policy(settings, workspace, opts) do
        turn_sandbox_policy = augment_runtime_turn_sandbox_policy(turn_sandbox_policy, workspace, opts)

        {:ok,
         %{
           approval_policy: settings.codex.approval_policy,
           thread_sandbox: settings.codex.thread_sandbox,
           turn_sandbox_policy: turn_sandbox_policy
         }}
      end
    end
  end

  defp augment_runtime_turn_sandbox_policy(policy, workspace, opts)
       when is_map(policy) and is_binary(workspace) do
    if Keyword.get(opts, :remote, false) or sandbox_policy_type(policy) != "workspaceWrite" do
      policy
    else
      roots = sandbox_policy_writable_roots(policy)
      extra_roots = workspace_git_metadata_roots(workspace)
      put_sandbox_policy_writable_roots(policy, dedupe_roots(roots ++ extra_roots))
    end
  end

  defp augment_runtime_turn_sandbox_policy(policy, _workspace, _opts), do: policy

  defp sandbox_policy_type(policy), do: Map.get(policy, "type") || Map.get(policy, :type)

  defp sandbox_policy_writable_roots(policy) do
    case Map.get(policy, "writableRoots") || Map.get(policy, :writableRoots) do
      roots when is_list(roots) -> roots
      _ -> []
    end
  end

  defp put_sandbox_policy_writable_roots(policy, roots) do
    cond do
      Map.has_key?(policy, "writableRoots") -> Map.put(policy, "writableRoots", roots)
      Map.has_key?(policy, :writableRoots) -> Map.put(policy, :writableRoots, roots)
      true -> Map.put(policy, "writableRoots", roots)
    end
  end

  defp workspace_git_metadata_roots(workspace) do
    workspace
    |> manifest_git_metadata_roots()
    |> Kernel.++(git_metadata_roots_from_git(workspace))
    |> Enum.filter(&is_binary/1)
    |> Enum.map(&Path.expand/1)
  end

  defp manifest_git_metadata_roots(workspace) do
    manifest_path = Path.join(workspace, ".harmony-workspace.json")

    with {:ok, raw} <- File.read(manifest_path),
         {:ok, %{"source" => source}} when is_map(source) <- Jason.decode(raw) do
      common_dir = source["git_common_dir"]
      git_dir = source["git_dir"]
      [common_dir, worktrees_dir(common_dir), git_dir]
    else
      _ -> []
    end
  end

  defp git_metadata_roots_from_git(workspace) do
    with {:ok, common_dir} <- git_value(workspace, ["rev-parse", "--path-format=absolute", "--git-common-dir"]),
         {:ok, git_dir} <- git_value(workspace, ["rev-parse", "--path-format=absolute", "--git-dir"]) do
      [common_dir, worktrees_dir(common_dir), git_dir]
    else
      _ -> []
    end
  end

  defp git_value(workspace, args) do
    case System.cmd("git", args, cd: workspace, stderr_to_stdout: true) do
      {value, 0} -> {:ok, String.trim(value)}
      _ -> :error
    end
  rescue
    _ -> :error
  end

  defp worktrees_dir(nil), do: nil
  defp worktrees_dir(path) when is_binary(path), do: Path.join(path, "worktrees")

  defp dedupe_roots(roots) do
    roots
    |> Enum.filter(&is_binary/1)
    |> Enum.reduce([], fn root, acc ->
      key = Path.expand(root)

      if Enum.any?(acc, &(Path.expand(&1) == key)) do
        acc
      else
        acc ++ [root]
      end
    end)
  end

  defp validate_semantics(settings) do
    cond do
      is_nil(settings.tracker.kind) ->
        {:error, :missing_tracker_kind}

      settings.tracker.kind not in ["linear", "memory"] ->
        {:error, {:unsupported_tracker_kind, settings.tracker.kind}}

      settings.tracker.kind == "linear" and not is_binary(settings.tracker.api_key) ->
        {:error, :missing_linear_api_token}

      settings.tracker.kind == "linear" and not is_binary(settings.tracker.project_slug) ->
        {:error, :missing_linear_project_slug}

      true ->
        :ok
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
