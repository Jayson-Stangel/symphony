defmodule SymphonyElixir.Codex.SandboxReadiness do
  @moduledoc "Credential-free startup qualification of the effective Codex sandbox."

  alias SymphonyElixir.Config
  alias SymphonyElixir.{Config.Schema, Workflow}

  @external_resource Path.expand("../../../priv/codex_readiness.py", __DIR__)
  @probe File.read!(@external_resource)
  @receipt_key :codex_sandbox_receipt

  @spec check(keyword()) :: :ok | {:error, String.t()}
  def check(opts \\ []) do
    Application.delete_env(:symphony_elixir, @receipt_key)
    execute = Keyword.get(opts, :execute, &System.cmd/3)

    load_settings = if Keyword.get(opts, :credential_free, false), do: &probe_settings/0, else: &Config.settings/0

    with {:ok, settings} <- load_settings.(),
         before <- fingerprint(settings),
         :ok <- if(settings.codex.thread_sandbox == "workspace-write", do: :ok, else: {:error, "sandbox_unsupported_policy"}),
         root <- Config.local_workspace_root(settings),
         :ok <- File.mkdir_p(root),
         {:ok, runtime} <- Config.resolve_codex_runtime_settings(settings, root, []),
         {:ok, python} <- dependency(opts, :python_path, "python3"),
         {:ok, timeout} <- dependency(opts, :timeout_path, "timeout"),
         input <- Jason.encode!(%{command: settings.codex.command, workspace: root, policy: runtime.turn_sandbox_policy, remote: settings.worker.ssh_hosts != []}),
         {data, 0} <- execute.(timeout, ["--kill-after=2", "20", python, "-c", @probe, input], stderr_to_stdout: true),
         {:ok, %{"ready" => true, "protocol" => 1}} <- Jason.decode(data),
         {:ok, current} <- load_settings.(),
         true <- before == fingerprint(current) do
      Application.put_env(:symphony_elixir, @receipt_key, before)
      :ok
    else
      {:error, code} when is_binary(code) ->
        {:error, code}

      {_data, status} when status in [124, 137] ->
        {:error, "sandbox_timeout"}

      {data, _status} when is_binary(data) ->
        case Jason.decode(data) do
          {:ok, %{"code" => code}} when is_binary(code) -> {:error, code}
          _ -> {:error, "sandbox_check_failed"}
        end

      _ ->
        {:error, "sandbox_check_failed"}
    end
  rescue
    _ -> {:error, "sandbox_check_failed"}
  end

  # Disposable diagnostics receive the workflow but deliberately receive no
  # tracker credentials. Parse the real sandbox inputs without authenticating
  # the unrelated tracker; listener startup still uses validated store settings.
  defp probe_settings do
    with {:ok, workflow} <- Workflow.load(), do: Schema.parse(workflow.config)
  end

  defp dependency(opts, key, command) do
    case Keyword.get_lazy(opts, key, fn -> System.find_executable(command) end) do
      path when is_binary(path) -> {:ok, path}
      _ -> {:error, "sandbox_check_dependency_missing_" <> command}
    end
  end

  @doc "Cheap process-local admission guard; never launches a helper on the scheduler."
  @spec qualified?(Path.t() | nil, Config.Schema.t() | nil) :: boolean()
  def qualified?(workspace \\ nil, settings \\ nil) do
    case Application.fetch_env(:symphony_elixir, @receipt_key) do
      {:ok, receipt} ->
        receipt == fingerprint(settings) and not local_config?(workspace || Config.local_workspace_root())

      :error ->
        false
    end
  rescue
    _ -> false
  end

  @doc false
  @spec fingerprint(Config.Schema.t() | nil) :: binary()
  def fingerprint(settings \\ nil) do
    settings = settings || Config.settings!()
    codex = Map.take(settings.codex, [:command, :thread_sandbox, :turn_sandbox_policy])
    environment = System.get_env() |> Map.take(["PATH", "HOME", "CODEX_HOME", "TMPDIR", "NODE_OPTIONS", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES"])
    executable = settings.codex.command |> String.split() |> List.first() |> System.find_executable()
    identity = if executable, do: {executable, file_identity(executable)}, else: nil
    home = System.get_env("CODEX_HOME") || Path.join(System.user_home!(), ".codex")
    configs = Enum.map([Path.join(home, "config.toml"), "/etc/codex/config.toml", "/etc/codex/requirements.toml"], &file_identity/1)
    workflow = SymphonyElixir.Workflow.workflow_file_path() |> Path.expand()
    :crypto.hash(:sha256, :erlang.term_to_binary({codex, settings.workspace, workflow, settings.worker.ssh_hosts, environment, identity, configs}))
  end

  defp file_identity(path) do
    case File.stat(path, time: :posix) do
      {:ok, stat} -> Map.take(stat, [:type, :size, :mtime, :ctime, :inode, :mode])
      error -> error
    end
  end

  defp local_config?(workspace) do
    File.exists?(Path.join(workspace, ".codex/config.toml")) or
      (workspace != Path.dirname(workspace) and local_config?(Path.dirname(workspace)))
  end
end
