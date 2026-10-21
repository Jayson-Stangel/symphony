defmodule SymphonyElixir.AgentPause do
  @moduledoc "Durable agent pause and unresolved tracker-call metadata in the project state volume."

  alias SymphonyElixir.Config

  @kinds ~w(quota_exhausted auth_required policy_blocked protocol_error tool_outcome_uncertain)
  @stored_kinds @kinds ++ ["operator_recheck", "operator_recheck_inflight", "probe_inflight"]
  @startup_codes ~w(auth_required unqualified_auth unknown_auth_status unsupported_tool_schema duplicate_tool_name
                    native_ownership_unavailable native_initialization_timeout unexpected_native_restart policy_blocked protocol_error runtime_unavailable)
  @rank %{
    "operator_recheck" => 0,
    "operator_recheck_inflight" => 0,
    "probe_inflight" => 0,
    "quota_exhausted" => 1,
    "auth_required" => 2,
    "policy_blocked" => 3
  }

  @spec current() :: :ready | {:paused | :probe, map()}
  def current do
    read_pause()
  end

  @doc "Legacy unresolved tracker calls are issue evidence, never a project dispatch gate."
  @spec recovery_notice(String.t()) :: String.t()
  def recovery_notice(issue_id) do
    case pending_tools() do
      {:ok, names} -> recovery_count_notice(Enum.count(names, &legacy_tool_for_issue?(&1, issue_id)))
      _ -> "\nLegacy tracker recovery evidence could not be read. Reconcile this issue from live tracker state before writing.\n"
    end
  end

  defp legacy_tool_for_issue?(name, issue_id) do
    path = Path.join(tool_root(), name)

    with {:ok, %{type: :regular, size: size}} when size <= 8192 <- File.lstat(path),
         {:ok, data} <- File.read(path),
         {:ok, %{"issue" => ^issue_id}} <- Jason.decode(data) do
      true
    else
      _ -> false
    end
  end

  defp recovery_count_notice(0), do: ""

  defp recovery_count_notice(count) do
    "\nLegacy recovery evidence: #{count} tracker call(s) for this issue lack confirmed delivery. Read back the current targets before replaying any write. These records do not prove whether a write executed.\n"
  end

  @doc false
  @spec safe_startup_code(term()) :: String.t()
  def safe_startup_code(code), do: if(code in @startup_codes, do: code, else: "runtime_unavailable")

  defp diagnostics(details) do
    code = details[:code] || details["code"] || details[:startup_code] || details["startup_code"]
    stage = details[:stage] || details["stage"]

    if (stage == "startup" and code in @startup_codes) or (stage == "cleanup" and code == "cleanup_restart_required"),
      do: %{"code" => code, "stage" => stage},
      else: %{}
  end

  @spec record(atom() | String.t(), map()) :: :ok | {:error, term()}
  def record(kind, details \\ %{}) do
    kind = to_string(kind)

    if kind in ["tool_outcome_uncertain", "protocol_error"] do
      :ok
    else
      serialized(fn -> record_pause(kind, details) end)
    end
  end

  defp record_pause(kind, details) do
    kind = if kind in @kinds, do: kind, else: "policy_blocked"
    old = previous_pause()
    profile = safe_profile(details[:profile] || details["profile"])
    reset = future_reset(details[:resets_at] || details["resets_at"])
    value = %{"version" => 1, "kind" => kind, "profile" => profile, "phase" => "waiting", "resets_at" => reset}
    atomic_write(pause_path(), merge_pause(old, Map.merge(value, diagnostics(details))))
  end

  @doc "Consumes only an explicit authentication/policy recheck before sending input."
  @spec begin_turn(String.t()) :: :ok | {:error, term()}
  def begin_turn(profile) do
    serialized(fn ->
      case current() do
        :ready ->
          :ok

        {:probe, %{"profile" => ^profile} = value} ->
          atomic_write(pause_path(), %{value | "kind" => "operator_recheck_inflight", "phase" => "waiting", "resets_at" => nil})

        _ ->
          {:error, :probe_not_available}
      end
    end)
  end

  @spec complete_probe(String.t()) :: :ok | {:error, term()}
  def complete_probe(profile) do
    serialized(fn ->
      case current() do
        {:probe, %{"profile" => ^profile}} -> remove_pause()
        {:paused, %{"kind" => kind, "profile" => ^profile}} when kind in ["probe_inflight", "operator_recheck_inflight"] -> remove_pause()
        :ready -> :ok
        {:paused, _value} -> :ok
        _ -> {:error, :pause_not_clearable}
      end
    end)
  end

  @doc "Clear only a non-quota recheck after confirmed cleanup for an issue state change."
  @spec cancel_recheck(String.t()) :: :ok | {:error, term()}
  def cancel_recheck(profile) do
    serialized(fn ->
      case current() do
        {:paused, %{"kind" => "operator_recheck_inflight", "profile" => ^profile}} -> remove_pause()
        _ -> :ok
      end
    end)
  end

  @spec explicit_recheck(String.t(), keyword()) :: :ok | {:error, term()}
  def explicit_recheck(profile, opts \\ []) do
    serialized(fn ->
      with :ok <- recheck_identity(profile),
           :ok <- reconcile_legacy_tools(Keyword.get(opts, :confirm_tools_reconciled, false)) do
        recheck_pause(read_pause(), profile)
      end
    end)
  end

  @doc "Logical Codex tool-state namespace, not an account identity or authentication attestation."
  @spec codex_profile() :: String.t()
  def codex_profile, do: Base.encode16(:crypto.hash(:sha256, "symphony:codex:tool-state:v1"), case: :lower)

  defp previous_pause do
    case read_pause() do
      {_, value} -> value
      :ready -> nil
    end
  end

  defp safe_profile(profile), do: if(valid_profile?(profile), do: profile)

  defp future_reset(reset) when is_integer(reset) do
    if reset > System.system_time(:second), do: reset
  end

  defp future_reset(_reset), do: nil

  defp merge_pause(nil, value), do: value

  defp merge_pause(old, value) do
    cond do
      Map.get(@rank, old["kind"], 10) > @rank[value["kind"]] ->
        old

      old["kind"] == "quota_exhausted" and value["kind"] == "quota_exhausted" and
          (is_nil(value["resets_at"]) or old["resets_at"] > value["resets_at"]) ->
        old

      true ->
        value
    end
  end

  defp recheck_pause(:ready, _profile), do: :ok

  defp recheck_pause({_, %{"kind" => "quota_exhausted", "resets_at" => reset}}, _profile) when is_integer(reset) and reset > 0 do
    if reset > System.system_time(:second), do: {:error, :reset_not_due}, else: remove_pause()
  end

  defp recheck_pause(_pause, profile), do: write_probe(profile)

  defp validate_pending_names(names) do
    if Enum.all?(names, &Regex.match?(~r/^[A-Za-z0-9_-]{1,128}\.json$/, &1)), do: {:ok, names}, else: {:error, :unknown_state_file}
  end

  defp reconcile_legacy_tools(false), do: :ok

  defp reconcile_legacy_tools(true) do
    with {:ok, pending} <- pending_tools(), do: reconcile_pending(pending)
  end

  defp reconcile_pending(pending) do
    Enum.reduce_while(pending, :ok, fn name, :ok ->
      case File.rm(Path.join(tool_root(), name)) do
        :ok -> {:cont, :ok}
        error -> {:halt, error}
      end
    end)
  end

  defp write_probe(profile, kind \\ "operator_recheck") do
    atomic_write(pause_path(), %{"version" => 1, "kind" => kind, "profile" => profile, "phase" => "probe", "resets_at" => nil})
  end

  defp read_pause do
    case File.read(pause_path()) do
      {:error, :enoent} ->
        :ready

      {:ok, data} when byte_size(data) <= 4096 ->
        with {:ok, %{"version" => 1, "kind" => kind, "phase" => phase} = value} <- Jason.decode(data),
             true <- kind in @stored_kinds and phase in ["waiting", "probe"],
             true <- valid_profile?(value["profile"]),
             true <- is_nil(value["resets_at"]) or (is_integer(value["resets_at"]) and value["resets_at"] >= 0),
             true <- valid_phase?(kind, phase) do
          public_pause(value)
        else
          _ -> {:paused, blocker("policy_blocked")}
        end

      _ ->
        {:paused, blocker("policy_blocked")}
    end
  end

  defp public_pause(%{"kind" => kind}) when kind in ["tool_outcome_uncertain", "protocol_error"], do: :ready

  # Quota is a retry delay, never a lease that a turn must finish to release.
  # Legacy consumed probes have no live owner after a restart or handoff.
  defp public_pause(%{"kind" => "probe_inflight"}), do: :ready

  defp public_pause(%{"kind" => "quota_exhausted"} = value) do
    reset = value["resets_at"]

    if is_integer(reset) and reset > System.system_time(:second),
      do: {:paused, Map.merge(Map.take(value, ~w(version kind phase profile resets_at)), diagnostics(value))},
      else: :ready
  end

  defp public_pause(value) do
    {if(value["phase"] == "probe", do: :probe, else: :paused), Map.merge(Map.take(value, ~w(version kind phase profile resets_at)), diagnostics(value))}
  end

  defp valid_phase?("operator_recheck", phase), do: phase == "probe"
  defp valid_phase?(kind, "probe"), do: kind == "quota_exhausted"
  defp valid_phase?(_kind, "waiting"), do: true

  defp pending_tools do
    case File.lstat(tool_root()) do
      {:error, :enoent} ->
        {:ok, []}

      {:ok, %{type: :directory}} ->
        with {:ok, names} <- File.ls(tool_root()), do: validate_pending_names(names)

      {:ok, _other} ->
        {:error, :unsafe_state_directory}

      error ->
        error
    end
  end

  defp atomic_write(path, value) do
    temp = Path.join(Config.agent_state_root(), ".agent-state-#{System.unique_integer([:positive])}")

    with :ok <- File.mkdir_p(Path.dirname(path)),
         :ok <- synced_write(temp, Jason.encode!(value)),
         :ok <- File.chmod(temp, 0o600),
         :ok <- File.rename(temp, path) do
      :ok
    else
      error ->
        File.rm(temp)
        error
    end
  end

  defp synced_write(path, data) do
    with {:ok, io} <- File.open(path, [:write, :binary, :exclusive]) do
      try do
        with :ok <- IO.binwrite(io, data), do: :file.sync(io)
      after
        File.close(io)
      end
    end
  end

  defp valid_profile?(nil), do: true
  defp valid_profile?(value) when is_binary(value), do: Regex.match?(~r/^[a-f0-9]{64}$/, value)
  defp valid_profile?(_value), do: false

  defp recheck_identity(profile) do
    if is_binary(profile) and valid_profile?(profile) do
      case read_pause() do
        {_, %{"profile" => expected}} when expected not in [nil, profile] -> {:error, :profile_changed}
        _ -> :ok
      end
    else
      {:error, :invalid_profile}
    end
  end

  defp remove_pause, do: File.rm(pause_path())

  defp blocker(kind), do: %{"version" => 1, "kind" => kind, "profile" => nil, "phase" => "waiting", "resets_at" => nil}
  defp pause_path, do: Path.join(Config.agent_state_root(), "claude-pause.json")
  defp tool_root, do: Path.join(Config.agent_state_root(), "claude-tools")
  defp serialized(fun), do: :global.trans({__MODULE__, self()}, fun)
end
