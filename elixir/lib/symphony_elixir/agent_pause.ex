defmodule SymphonyElixir.AgentPause do
  @moduledoc "Durable agent pause and unresolved tracker-call metadata in the project state volume."

  alias SymphonyElixir.Config

  @kinds ~w(quota_exhausted auth_required policy_blocked protocol_error tool_outcome_uncertain)
  @stored_kinds @kinds ++ ["operator_recheck", "operator_recheck_inflight", "probe_inflight"]
  @rank %{
    "operator_recheck" => 0,
    "operator_recheck_inflight" => 0,
    "probe_inflight" => 0,
    "quota_exhausted" => 1,
    "auth_required" => 2,
    "policy_blocked" => 3,
    "protocol_error" => 4,
    "tool_outcome_uncertain" => 5
  }

  @spec current() :: :ready | {:paused | :probe, map()}
  def current do
    case pending_tools() do
      {:ok, []} -> read_pause()
      {:ok, _pending} -> {:paused, blocker("tool_outcome_uncertain")}
      {:error, _reason} -> {:paused, blocker("policy_blocked")}
    end
  end

  @spec record(atom() | String.t(), map()) :: :ok | {:error, term()}
  def record(kind, details \\ %{}) do
    serialized(fn ->
      kind = to_string(kind)
      kind = if kind in @kinds, do: kind, else: "policy_blocked"

      old = previous_pause()
      profile = safe_profile(details[:profile] || details["profile"])
      reset = future_reset(details[:resets_at] || details["resets_at"])
      value = %{"version" => 1, "kind" => kind, "profile" => profile, "phase" => "waiting", "resets_at" => reset}
      value = merge_pause(old, value)

      atomic_write(pause_path(), value)
    end)
  end

  @spec claim_due_probe() :: :ready | {:paused | :probe, map()}
  def claim_due_probe do
    serialized(fn ->
      claim_probe(current())
    end)
  end

  @doc "Consumes a durable probe before sending input, so a failed attempt or restart cannot replay it."
  @spec begin_turn(String.t()) :: :ok | {:error, term()}
  def begin_turn(profile) do
    serialized(fn ->
      case current() do
        :ready ->
          :ok

        {:probe, %{"profile" => ^profile} = value} ->
          kind = inflight_kind(value["kind"])
          atomic_write(pause_path(), %{value | "kind" => kind, "phase" => "waiting", "resets_at" => nil})

        _ ->
          {:error, :probe_not_available}
      end
    end)
  end

  defp inflight_kind("operator_recheck"), do: "operator_recheck_inflight"
  defp inflight_kind(_kind), do: "probe_inflight"

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
      with {:ok, pending} <- pending_tools(true),
           :ok <- recheck_identity(profile),
           :ok <- reconcile_pending(pending, Keyword.get(opts, :confirm_tools_reconciled, false)) do
        recheck_pause(read_pause(), profile)
      end
    end)
  end

  @spec register_tool(String.t()) :: :ok
  def register_tool(tool_id) do
    serialized(fn ->
      owners = :persistent_term.get({__MODULE__, :tool_owners}, %{}) |> Map.filter(fn {_key, pid} -> Process.alive?(pid) end)
      :persistent_term.put({__MODULE__, :tool_owners}, Map.put(owners, {Config.agent_state_root(), tool_id}, self()))
    end)
  end

  @spec unregister_tool(String.t()) :: :ok
  def unregister_tool(tool_id) do
    serialized(fn ->
      owners = :persistent_term.get({__MODULE__, :tool_owners}, %{})
      :persistent_term.put({__MODULE__, :tool_owners}, Map.delete(owners, {Config.agent_state_root(), tool_id}))
    end)
  end

  @spec begin_tool(String.t(), String.t(), String.t(), String.t(), map()) :: :ok | {:error, term()}
  def begin_tool(profile, issue, tool_id, name, diagnostics \\ %{}) do
    with :ok <- validate_tool_id(tool_id),
         :ok <- File.mkdir_p(tool_root()),
         {:ok, %{type: :directory}} <- File.lstat(tool_root()),
         {:ok, io} <- File.open(tool_path(tool_id), [:write, :binary, :exclusive]) do
      try do
        with :ok <- IO.binwrite(io, Jason.encode!(%{version: 1, profile: profile, issue: issue, tool: name, diagnostics: diagnostics})),
             :ok <- :file.sync(io) do
          File.chmod(tool_path(tool_id), 0o600)
        end
      after
        File.close(io)
      end
    end
  end

  @spec annotate_tool(String.t(), map()) :: :ok | {:error, term()}
  def annotate_tool(tool_id, diagnostics) do
    with :ok <- validate_tool_id(tool_id),
         {:ok, data} <- File.read(tool_path(tool_id)),
         {:ok, value} <- Jason.decode(data) do
      atomic_write(tool_path(tool_id), Map.put(value, "diagnostics", diagnostics))
    end
  end

  @doc "Logical Codex tool-state namespace, not an account identity or authentication attestation."
  @spec codex_profile() :: String.t()
  def codex_profile, do: Base.encode16(:crypto.hash(:sha256, "symphony:codex:tool-state:v1"), case: :lower)

  @spec finish_tool(String.t()) :: :ok | {:error, term()}
  def finish_tool(tool_id) do
    with :ok <- validate_tool_id(tool_id), do: File.rm(tool_path(tool_id))
  end

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
      Map.get(@rank, old["kind"], 10) > @rank[value["kind"]] -> old
      old["phase"] == "probe" && old["resets_at"] == value["resets_at"] -> %{value | "resets_at" => nil}
      true -> value
    end
  end

  defp claim_probe({:paused, %{"kind" => "quota_exhausted", "resets_at" => reset} = value}) when is_integer(reset) do
    if reset <= System.system_time(:second), do: persist_probe(value), else: {:paused, value}
  end

  defp claim_probe(result), do: result

  defp persist_probe(value) do
    probe = Map.put(value, "phase", "probe")

    case atomic_write(pause_path(), probe) do
      :ok -> {:probe, probe}
      _ -> {:paused, blocker("policy_blocked")}
    end
  end

  defp recheck_pause(:ready, _profile), do: :ok

  defp recheck_pause({_, %{"kind" => "quota_exhausted", "resets_at" => reset}}, profile) when is_integer(reset) and reset > 0 do
    if reset > System.system_time(:second), do: {:error, :reset_not_due}, else: write_probe(profile, "quota_exhausted")
  end

  defp recheck_pause({_, %{"kind" => kind}}, profile) when kind in ["quota_exhausted", "probe_inflight"],
    do: write_probe(profile, "quota_exhausted")

  defp recheck_pause(_pause, profile), do: write_probe(profile)

  defp validate_pending_names(names) do
    if Enum.all?(names, &Regex.match?(~r/^[A-Za-z0-9_-]{1,128}\.json$/, &1)), do: {:ok, names}, else: {:error, :unknown_state_file}
  end

  defp reconcile_pending([], _confirmed), do: :ok
  defp reconcile_pending(_pending, false), do: {:error, :tool_reconciliation_required}

  defp reconcile_pending(pending, true) do
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
          {if(phase == "probe", do: :probe, else: :paused), Map.take(value, ~w(version kind phase profile resets_at))}
        else
          _ -> {:paused, blocker("policy_blocked")}
        end

      _ ->
        {:paused, blocker("policy_blocked")}
    end
  end

  defp valid_phase?("operator_recheck", phase), do: phase == "probe"
  defp valid_phase?(kind, "probe"), do: kind == "quota_exhausted"
  defp valid_phase?(_kind, "waiting"), do: true

  defp pending_tools(include_active \\ false) do
    case File.lstat(tool_root()) do
      {:error, :enoent} ->
        {:ok, []}

      {:ok, %{type: :directory}} ->
        with {:ok, names} <- File.ls(tool_root()),
             {:ok, names} <- validate_pending_names(names) do
          owners = :persistent_term.get({__MODULE__, :tool_owners}, %{})
          {:ok, Enum.reject(names, &active_tool?(&1, owners, include_active))}
        end

      {:ok, _other} ->
        {:error, :unsafe_state_directory}

      error ->
        error
    end
  end

  defp active_tool?(name, owners, include_active) do
    owner = Map.get(owners, {Config.agent_state_root(), Path.rootname(name)})
    not include_active and is_pid(owner) and Process.alive?(owner)
  end

  defp validate_tool_id(value) when is_binary(value) do
    if Regex.match?(~r/^[A-Za-z0-9_-]{1,128}$/, value), do: :ok, else: {:error, :invalid_tool_id}
  end

  defp validate_tool_id(_value), do: {:error, :invalid_tool_id}

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
  defp tool_path(id), do: Path.join(tool_root(), id <> ".json")
  defp serialized(fun), do: :global.trans({__MODULE__, self()}, fun)
end
