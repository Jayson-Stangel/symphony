defmodule SymphonyElixir.AgentPause do
  @moduledoc "Claude account pauses and issue-local legacy tracker evidence in the project state volume."

  alias SymphonyElixir.Config

  @kinds ~w(quota_exhausted auth_required policy_blocked protocol_error tool_outcome_uncertain)
  @stored_kinds @kinds ++ ["operator_recheck", "operator_recheck_inflight", "probe_inflight"]
  @startup_codes ~w(auth_required unqualified_auth unknown_auth_status unsupported_tool_schema duplicate_tool_name
                    native_ownership_unavailable native_initialization_timeout unexpected_native_restart policy_blocked protocol_error runtime_unavailable)
  @rank %{
    "quota_exhausted" => 1,
    "auth_required" => 2,
    "policy_blocked" => 3
  }

  @spec current() :: :ready | {:paused, map()}
  def current do
    if Config.coding_agent() == :claude, do: read_pause(), else: :ready
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
    if Config.coding_agent() == :claude, do: record_claude(kind, details), else: :ok
  end

  defp record_claude(kind, details) do
    kind = to_string(kind)

    if kind in ["tool_outcome_uncertain", "protocol_error"],
      do: :ok,
      else: serialized(fn -> record_pause(kind, details) end)
  end

  defp record_pause(kind, details) do
    kind = if kind in @kinds, do: kind, else: "policy_blocked"
    old = previous_pause()
    profile = safe_profile(details[:profile] || details["profile"])
    reset = future_reset(details[:resets_at] || details["resets_at"])
    value = %{"version" => 1, "kind" => kind, "profile" => profile, "phase" => "waiting", "resets_at" => reset}
    atomic_write(pause_path(), merge_pause(old, Map.merge(value, diagnostics(details))))
  end

  @spec explicit_recheck(String.t(), keyword()) :: :ok | {:error, term()}
  def explicit_recheck(profile, opts \\ []) do
    if Config.coding_agent() == :claude, do: recheck_claude(profile, opts), else: :ok
  end

  defp recheck_claude(profile, opts) do
    serialized(fn ->
      with true <- is_binary(profile) and valid_profile?(profile),
           {:ok, pause} <- load_pause(),
           :ok <- recheck_quota(pause),
           :ok <- reconcile_legacy_tools(Keyword.get(opts, :confirm_tools_reconciled, false)) do
        recheck_pause(pause)
      else
        false -> {:error, :invalid_profile}
        error -> error
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

  defp recheck_quota(%{"kind" => "quota_exhausted", "resets_at" => reset}) when is_integer(reset) and reset > 0 do
    if reset > System.system_time(:second), do: {:error, :reset_not_due}, else: :ok
  end

  defp recheck_quota(_pause), do: :ok
  defp recheck_pause(nil), do: :ok
  defp recheck_pause(pause), do: if(public_pause(pause) == :ready, do: :ok, else: remove_pause())

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

  defp read_pause do
    case load_pause() do
      {:ok, nil} -> :ready
      {:ok, value} -> public_pause(value)
      {:error, _reason} -> {:paused, blocker("policy_blocked")}
    end
  end

  defp load_pause do
    with {:ok, %{type: :regular, size: size}} when size <= 4096 <- File.lstat(pause_path()),
         {:ok, data} when byte_size(data) <= 4096 <- File.read(pause_path()),
         {:ok, %{"version" => 1, "kind" => kind, "phase" => phase} = value} <- Jason.decode(data),
         true <- kind in @stored_kinds and phase in ["waiting", "probe"],
         true <- valid_profile?(value["profile"]),
         true <- is_nil(value["resets_at"]) or (is_integer(value["resets_at"]) and value["resets_at"] >= 0),
         true <- valid_phase?(kind, phase) do
      {:ok, value}
    else
      {:error, :enoent} -> {:ok, nil}
      {:error, reason} when is_atom(reason) -> {:error, reason}
      _ -> {:error, :invalid_pause}
    end
  end

  # Retired cleanup/ownership proof stops are identified by persisted diagnostics.
  # Preserve ambiguous untagged policy records for the existing explicit recheck.
  defp public_pause(%{"kind" => "policy_blocked", "code" => "cleanup_restart_required", "stage" => "cleanup"}), do: :ready
  defp public_pause(%{"kind" => "policy_blocked", "code" => "native_ownership_unavailable", "stage" => "startup"}), do: :ready

  # This exact unscoped startup record was emitted for helper transport failures
  # before hello. Preserve its bytes, but do not recreate the retired policy gate.
  defp public_pause(%{"version" => 1, "kind" => "policy_blocked", "profile" => nil, "phase" => "waiting", "resets_at" => nil, "code" => "protocol_error", "stage" => "startup"} = value)
       when map_size(value) == 7,
       do: :ready

  # The exact original unscoped auth record came only from pre-input observations.
  # Unknown fields or newer diagnostic-bearing auth stops are not legacy matches.
  defp public_pause(%{"version" => 1, "kind" => "auth_required", "profile" => nil, "phase" => "waiting", "resets_at" => nil} = value)
       when map_size(value) == 5,
       do: :ready

  defp public_pause(%{"kind" => kind}) when kind in ["tool_outcome_uncertain", "protocol_error"], do: :ready

  # These historical scheduler permits are not unresolved native/tool outcomes.
  # Preserve the files without recreating a turn-completion lease.
  defp public_pause(%{"kind" => kind}) when kind in ["operator_recheck", "operator_recheck_inflight", "probe_inflight"], do: :ready

  defp public_pause(%{"kind" => "quota_exhausted"} = value) do
    reset = value["resets_at"]

    if is_integer(reset) and reset > System.system_time(:second),
      do: {:paused, Map.merge(Map.take(value, ~w(version kind phase profile resets_at)), diagnostics(value))},
      else: :ready
  end

  defp public_pause(value) do
    {:paused, Map.merge(Map.take(value, ~w(version kind phase profile resets_at)), diagnostics(value))}
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

  defp remove_pause, do: File.rm(pause_path())

  defp blocker(kind), do: %{"version" => 1, "kind" => kind, "profile" => nil, "phase" => "waiting", "resets_at" => nil}
  defp pause_path, do: Path.join(Config.agent_state_root(), "claude-pause.json")
  defp tool_root, do: Path.join(Config.agent_state_root(), "claude-tools")
  defp serialized(fun), do: :global.trans({__MODULE__, self()}, fun)
end
