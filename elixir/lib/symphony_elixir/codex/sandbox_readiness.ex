defmodule SymphonyElixir.Codex.SandboxReadiness do
  @moduledoc "Credential-free startup qualification of the effective Codex sandbox."

  alias SymphonyElixir.Config
  alias SymphonyElixir.{Config.Schema, Workflow}

  @external_resource Path.expand("../../../priv/codex_readiness.py", __DIR__)
  @boundary_resource Path.expand("../../../priv/codex_config_boundary.py", __DIR__)
  @external_resource @boundary_resource
  @launch_resource Path.expand("../../../priv/codex_launch.py", __DIR__)
  @external_resource @launch_resource
  @boundary File.read!(@boundary_resource)
  @launcher @boundary <> "\n" <> File.read!(@launch_resource)
  @probe @boundary <> "\n" <> File.read!(Path.expand("../../../priv/codex_readiness.py", __DIR__))
  @receipt_key :codex_sandbox_receipt

  @spec check(keyword()) :: :ok | {:error, String.t()}
  def check(opts \\ []) do
    Application.delete_env(:symphony_elixir, @receipt_key)

    load_settings = if Keyword.get(opts, :credential_free, false), do: &probe_settings/0, else: &Config.settings/0

    with {:ok, settings} <- load_settings.(),
         before <- fingerprint(settings),
         root <- Config.local_workspace_root(settings),
         :ok <- execute_probe(settings, root, launch_root(), opts),
         {:ok, current} <- load_settings.(),
         true <- before == fingerprint(current) do
      Application.put_env(:symphony_elixir, @receipt_key, before)
      :ok
    else
      {:error, code} when is_binary(code) ->
        {:error, code}

      _ ->
        {:error, "sandbox_check_failed"}
    end
  rescue
    _ -> {:error, "sandbox_check_failed"}
  end

  @doc "Recheck wrapped launches in their post-hook task workspace, off the scheduler."
  @spec check_workspace(Path.t(), Schema.t(), keyword()) :: :ok | {:error, String.t()}
  def check_workspace(workspace, settings, opts \\ []) do
    cond do
      System.get_env("SYMPHONY_CONTAINERIZED") == "1" ->
        # Full config parsing and native effects stay off the scheduler.
        execute_probe(settings, workspace, workspace, opts)

      wrapped_command?(settings.codex.command) ->
        execute_probe(settings, workspace, workspace, opts)

      OptionParser.split(settings.codex.command) != [] ->
        :ok

      true ->
        {:error, "sandbox_unsupported_command"}
    end
  rescue
    _ -> {:error, "sandbox_check_failed"}
  end

  defp execute_probe(settings, workspace, source, opts) do
    execute = Keyword.get(opts, :execute, &System.cmd/3)

    with :ok <- if(settings.codex.thread_sandbox == "workspace-write", do: :ok, else: {:error, "sandbox_unsupported_policy"}),
         :ok <- File.mkdir_p(workspace),
         {:ok, runtime} <- Config.resolve_codex_runtime_settings(settings, workspace, []),
         {:ok, python} <- dependency(opts, :python_path, "python3"),
         {:ok, timeout} <- dependency(opts, :timeout_path, "timeout"),
         input <-
           Jason.encode!(
             Map.merge(
               %{
                 command: settings.codex.command,
                 workspace: workspace,
                 managed_workspace: Config.local_workspace_root(settings),
                 launch_root: source,
                 policy: runtime.turn_sandbox_policy,
                 containerized: System.get_env("SYMPHONY_CONTAINERIZED") == "1",
                 remote: settings.worker.ssh_hosts != []
               },
               boundary_input(settings, workspace)
             )
           ),
         {data, 0} <- execute.(timeout, ["--kill-after=2", "190", python, "-I", "-c", @probe, input], stderr_to_stdout: true),
         {:ok, %{"ready" => true, "protocol" => 1} = receipt} <- Jason.decode(data) do
      probe_receipt(receipt, Keyword.get(opts, :return_chain, false) and snapshot_profile?())
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
  end

  @doc "Qualify and bind one session's actual post-setup executable closure."
  @spec qualified_command(String.t(), Path.t(), Schema.t()) :: {:ok, String.t()} | {:error, String.t()}
  def qualified_command(command, workspace, settings) do
    if managed_snapshot?() do
      with {:ok, chain} <- execute_probe(settings, workspace, workspace, return_chain: true),
           do: bound_command(command, workspace, settings, chain)
    else
      with :ok <- check_workspace(workspace, settings), do: {:ok, command}
    end
  end

  @doc "Bind the actual managed launch after trusted wrapper setup, before native config loading."
  @spec bound_command(String.t(), Path.t(), Schema.t(), map()) :: {:ok, String.t()} | {:error, String.t()}
  def bound_command(command, workspace, settings, chain) do
    if managed_snapshot?() do
      {prefix, native} = launch_parts(settings.codex.command)

      case System.find_executable("python3") do
        python when is_binary(python) ->
          input =
            boundary_input(settings, workspace)
            |> Map.merge(%{
              operation: "exec",
              expected_launch_chain: chain,
              expected_image_chain: Map.delete(chain, "node"),
              workspace: workspace,
              managed_workspace: Config.local_workspace_root(settings),
              launch_root: workspace
            })

          args = prefix ++ [python, "-I", "-c", @launcher, Jason.encode!(input)] ++ native
          {:ok, Enum.map_join(args, " ", &shell_quote/1)}

        _ ->
          {:error, "sandbox_launch_unavailable"}
      end
    else
      {:ok, command}
    end
  rescue
    _ -> {:error, "sandbox_unqualified_config"}
  end

  defp boundary_input(settings, workspace) do
    if snapshot_profile?() do
      {_prefix, native} = launch_parts(settings.codex.command)

      executable = native |> hd() |> System.find_executable() |> canonical_executable(16)
      digest = executable |> File.read!() |> then(&:crypto.hash(:sha256, &1)) |> Base.encode16(case: :lower)

      env =
        System.get_env()
        |> Enum.filter(fn {key, _} ->
          key != "CODEX_HOME" and
            Enum.any?(["CODEX_", "OPENAI_", "AZURE_OPENAI_"], &String.starts_with?(key, &1))
        end)
        |> Map.new(fn {key, value} -> {key, Base.encode16(:crypto.hash(:sha256, value), case: :lower)} end)

      %{
        snapshot_profile: System.get_env("SYMPHONY_CODEX_CONFIG_PROFILE"),
        expected_snapshot_uid: snapshot_owner(),
        expected_snapshot_sha256: System.get_env("SYMPHONY_CODEX_CONFIG_SHA256"),
        expected_home: System.user_home!(),
        expected_codex_home: System.get_env("CODEX_HOME") || Path.join(System.user_home!(), ".codex"),
        expected_executable: executable,
        expected_executable_sha256: digest,
        expected_native_environment: env,
        managed_workspace: Config.local_workspace_root(settings),
        workspace: workspace,
        launch_program: @launcher
      }
    else
      %{}
    end
  end

  defp snapshot_profile?, do: System.get_env("SYMPHONY_CODEX_CONFIG_PROFILE") in ["trust-ro-v1", "trust-ro-v2"]

  defp managed_snapshot?, do: snapshot_profile?() and System.get_env("SYMPHONY_CONTAINERIZED") == "1"

  defp wrapped_command?(command) do
    case OptionParser.split(command) do
      [shell | _] -> Path.basename(shell) == "bash"
      _ -> false
    end
  end

  defp launch_parts(command) do
    words = OptionParser.split(command)

    case words do
      [shell, script | rest] -> if Path.basename(shell) == "bash", do: {[shell, script], rest}, else: {[], words}
      _ -> {[], words}
    end
  end

  defp probe_receipt(%{"launch_chain" => chain}, true) when is_map(chain), do: {:ok, chain}
  defp probe_receipt(_receipt, true), do: {:error, "sandbox_unqualified_config"}
  defp probe_receipt(_receipt, false), do: :ok

  defp snapshot_owner do
    case Integer.parse(System.get_env("SYMPHONY_CODEX_CONFIG_OWNER") || "") do
      {owner, ""} when owner >= 0 and owner <= 4_294_967_294 -> owner
      _ -> nil
    end
  end

  defp canonical_executable(path, remaining) when is_binary(path) and remaining > 0 do
    case File.read_link(path) do
      {:ok, target} -> canonical_executable(Path.expand(target, Path.dirname(path)), remaining - 1)
      {:error, :einval} -> Path.expand(path)
    end
  end

  defp canonical_executable(_, _), do: raise("qualified executable link limit")

  defp shell_quote(value), do: "'" <> String.replace(value, "'", "'\\''") <> "'"

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

    environment =
      System.get_env()
      |> Map.take([
        "PATH",
        "HOME",
        "CODEX_HOME",
        "TMPDIR",
        "NODE_OPTIONS",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "SYMPHONY_LAUNCH_ROOT",
        "SYMPHONY_CONTAINERIZED",
        "SYMPHONY_CODEX_CONFIG_PROFILE",
        "SYMPHONY_CODEX_CONFIG_SHA256",
        "SYMPHONY_CODEX_CONFIG_OWNER"
      ])

    words = OptionParser.split(settings.codex.command)
    executable = words |> List.first() |> System.find_executable()

    wrapper =
      case words do
        [shell, script | _] ->
          if Path.basename(shell) == "bash", do: {launch_root(), script, File.read(Path.expand(script, launch_root()))}, else: nil

        _ ->
          nil
      end

    identity = if executable, do: {executable, file_identity(executable)}, else: nil
    home = System.get_env("CODEX_HOME") || Path.join(System.user_home!(), ".codex")
    configs = Enum.map([Path.join(home, "config.toml"), "/etc/codex/config.toml", "/etc/codex/requirements.toml"], &file_identity/1)
    workflow = SymphonyElixir.Workflow.workflow_file_path() |> Path.expand()
    :crypto.hash(:sha256, :erlang.term_to_binary({codex, settings.workspace, workflow, settings.worker.ssh_hosts, environment, identity, configs, wrapper}))
  end

  defp launch_root do
    System.get_env("SYMPHONY_LAUNCH_ROOT") ||
      Workflow.workflow_file_path() |> Path.expand() |> Path.dirname()
  end

  defp file_identity(path) do
    case File.lstat(path, time: :posix) do
      {:ok, stat} -> Map.take(stat, [:type, :size, :mtime, :ctime, :inode, :mode])
      error -> error
    end
  end

  defp local_config?(workspace) do
    File.exists?(Path.join(workspace, ".codex/config.toml")) or
      (workspace != Path.dirname(workspace) and local_config?(Path.dirname(workspace)))
  end
end
