defmodule SymphonyElixir.Claude.Adapter do
  @moduledoc "CodingAgent transport for the pinned, subscription-authenticated SDK helper."
  @behaviour SymphonyElixir.CodingAgent

  require Logger
  alias SymphonyElixir.{AgentPause, AgentReadiness, Config, ToolCall, Tracker, Workspace}
  alias SymphonyElixir.Claude.{Instructions, Skills}

  @max_frame 1_048_576
  @pause_kinds ~w(quota_exhausted auth_required policy_blocked protocol_error tool_outcome_uncertain)
  @codes %{
    "auth_required" => :auth_required,
    "quota_exhausted" => :quota_exhausted,
    "policy_blocked" => :policy_blocked,
    "protocol_error" => :protocol_error,
    "tool_outcome_uncertain" => :tool_outcome_uncertain
  }

  @impl true
  def start_session(workspace, opts) do
    case Keyword.get(opts, :worker_host) do
      nil -> start_local_session(workspace, opts)
      _ -> {:error, :claude_ssh_unsupported}
    end
  end

  defp start_local_session(workspace, opts) do
    with {:ok, workspace} <- Workspace.validate_agent_cwd(workspace),
         {:ok, instructions} <- Instructions.context(workspace),
         {:ok, skills} <- Skills.sources(workspace),
         binding <- Keyword.get_lazy(opts, :tool_binding, &Tracker.bind_agent_tools/0),
         {:ok, env} <- Config.claude_child_environment(System.get_env(), binding.secret_environment_names),
         {:ok, port} <- start_port(workspace, env, opts) do
      initialize_or_close(port, workspace, instructions, skills, Keyword.put(opts, :tool_binding, binding))
    end
  end

  defp initialize_or_close(port, workspace, instructions, skills, opts) do
    id = new_id()
    request = new_id()

    case initialize(port, workspace, instructions, skills, opts, id, request) do
      {:ok, session} ->
        {:ok, session}

      {:error, {:readiness_observation, code}} ->
        close_port(port)

        if AgentReadiness.observation_code?(code),
          do: {:error, {:claude_startup_failed, code}},
          else: paused_start(code)

      {:error, {:before_start, code}} ->
        close_port(port)
        startup_failure(code, :ok)

      {:error, {:stopped, code}} ->
        close_port(port)
        startup_failure(code, :ok)

      {:error, code} ->
        # Attempt bounded cleanup before reporting the original startup failure.
        # Missing confirmation is diagnostic, not a project-wide recovery gate.
        write(port, %{v: 1, type: "stop", session_id: id, request_id: request})
        cleanup = await_startup_stop(port, deadline(5_000), id, request)
        close_port(port)
        startup_failure(code, cleanup)
    end
  end

  defp initialize(port, workspace, instructions, skills, opts, id, start_id) do
    case frame(port, deadline(35_000)) do
      {:ok, %{"v" => 1, "type" => "hello", "protocol" => 1, "sdk" => sdk, "cli" => cli, "policy" => 1}}
      when is_binary(sdk) and byte_size(sdk) in 1..128 and is_binary(cli) and byte_size(cli) in 1..128 ->
        initialize_native(port, workspace, instructions, skills, opts, id, start_id)

      {:ok, %{"ready" => false, "code" => code}} ->
        {:error, {:readiness_observation, code}}

      {:error, cause} ->
        {:error, {:before_start, cause}}

      _ ->
        {:error, {:before_start, "protocol_error"}}
    end
  end

  defp initialize_native(port, workspace, instructions, skills, opts, id, start_id) do
    config = Config.settings!().claude
    binding = Keyword.fetch!(opts, :tool_binding)

    with :ok <-
           write(port, %{
             v: 1,
             type: "start",
             session_id: id,
             request_id: start_id,
             workspace: workspace,
             config: Map.take(Map.from_struct(config), [:model, :effort, :max_agentic_turns, :turn_timeout_ms]),
             tools: binding.tool_specs,
             skills: skills,
             instructions: instructions
           }),
         {:ok, %{"v" => 1, "type" => "ready", "session_id" => ^id, "request_id" => ^start_id, "identity" => %{"profile" => profile} = identity, "owned_native" => owned}} <-
           frame(port, deadline(15_000)),
         true <- valid_profile?(profile) and valid_owned?(owned),
         :ok <- match_expected_profile(profile, Keyword.get(opts, :expected_profile)) do
      {:ok,
       %{
         port: port,
         owner: self(),
         id: id,
         workspace: workspace,
         config: config,
         binding: binding,
         identity: identity,
         owned_native: owned,
         helper_identity: port_identity(port),
         node: Keyword.get(opts, :node_path, Config.claude_node_path()),
         cleanup: Keyword.get(opts, :cleanup_path, Path.join(Path.dirname(Keyword.get(opts, :helper_path, Config.claude_helper_path())), "cleanup.mjs"))
       }}
    else
      {:ok, %{"v" => 1, "type" => "stopped", "session_id" => ^id, "request_id" => ^start_id}} -> {:error, {:stopped, "protocol_error"}}
      {:error, :native_identity_mismatch} -> {:error, "policy_blocked"}
      false -> {:error, "native_ownership_unavailable"}
      {:ok, %{"ready" => false, "code" => code}} -> {:error, code}
      {:ok, %{"type" => "error", "code" => code}} -> {:error, code}
      _ -> {:error, "protocol_error"}
    end
  end

  defp await_startup_stop(port, until, id, request) do
    case frame(port, until) do
      {:ok, %{"v" => 1, "type" => "stopped", "session_id" => ^id, "request_id" => ^request}} -> :ok
      {:ok, _other} -> await_startup_stop(port, until, id, request)
      _ -> {:error, :cleanup_unconfirmed}
    end
  end

  defp startup_failure({:helper_exited, _status} = cause, :ok), do: {:error, {:claude_startup_failed, cause}}

  defp startup_failure(code, :ok)
       when code in [:timeout, :invalid_frame, :frame_too_large, :interrupted, "protocol_error", "native_initialization_timeout", "unexpected_native_restart", "native_ownership_unavailable"],
       do: {:error, {:claude_startup_failed, code}}

  defp startup_failure(code, :ok), do: paused_start(code)

  defp startup_failure(code, {:error, :cleanup_unconfirmed}) do
    Logger.warning("Claude startup cleanup incomplete stage=cleanup startup_code=#{AgentPause.safe_startup_code(code)} cleanup=cleanup_unconfirmed")
    startup_failure(code, :ok)
  end

  defp match_expected_profile(_profile, nil), do: :ok
  defp match_expected_profile(profile, profile), do: :ok
  defp match_expected_profile(_profile, _expected), do: {:error, :native_identity_mismatch}

  @impl true
  def run_turn(%{owner: owner} = session, prompt, issue, opts) when owner == self() do
    case AgentPause.current() do
      {:paused, value} ->
        pause(session, value["kind"], Map.take(value, ["resets_at", "code", "stage"]))

      :ready ->
        do_run_turn(session, prompt, issue, opts)
    end
  end

  def run_turn(_session, _prompt, _issue, _opts), do: {:error, :wrong_session_owner}

  defp do_run_turn(session, prompt, issue, opts) do
    request = new_id()
    event_id = "claude:#{session.id}:#{request}"
    callback = Keyword.get(opts, :on_message, fn _ -> :ok end)

    case write(session.port, %{v: 1, type: "turn", session_id: session.id, request_id: request, prompt: prompt}) do
      :ok ->
        emit(callback, :session_started, %{session_id: event_id, provider: :claude})
        await_turn(session, request, event_id, issue, callback, deadline(session.config.turn_timeout_ms), %{})

      {:error, :port_closed} ->
        {:error, :claude_helper_closed}

      _ ->
        pause(session, "protocol_error", %{})
    end
  end

  @spec await_turn(map(), String.t(), String.t(), map(), function(), integer(), map()) :: term()
  defp await_turn(session, request, event_id, issue, callback, until, pending) do
    case frame(session.port, min(until, deadline(session.config.read_timeout_ms))) do
      {:error, :timeout} ->
        if System.monotonic_time(:millisecond) >= until do
          pending_failure(session, pending, :claude_turn_timeout)
        else
          await_turn(session, request, event_id, issue, callback, until, pending)
        end

      {:error, :interrupted} ->
        {:error, :claude_cancelled}

      {:error, {:helper_exited, _status}} ->
        pending_failure(session, pending, :claude_helper_exited)

      {:error, :invalid_frame} ->
        pending_failure(session, pending, :claude_invalid_frame)

      {:error, :frame_too_large} ->
        pending_failure(session, pending, :claude_frame_too_large)

      {:ok, %{"v" => 1, "session_id" => id, "request_id" => ^request} = message} when id == session.id ->
        handle_message(message, session, request, event_id, issue, callback, until, pending)

      _ ->
        pending_failure(session, pending)
    end
  end

  @spec handle_message(map(), map(), String.t(), String.t(), map(), function(), integer(), map()) :: term()
  defp handle_message(%{"type" => "progress", "activity" => activity} = message, session, request, event_id, issue, callback, until, pending)
       when activity in ["agent", "tool"] do
    {text, advances?} = progress_summary(message)
    productive = advances? and message["productive"] == true
    details = %{session_id: event_id, activity: activity, provider: :claude, payload: text, productive: productive}
    emit(callback, :agent_progress, details)
    await_turn(session, request, event_id, issue, callback, until, pending)
  end

  defp handle_message(%{"type" => "usage", "totals" => totals}, session, request, event_id, issue, callback, until, pending) do
    if valid_usage?(totals) do
      payload = %{"tokenUsage" => %{"total" => totals}}
      emit(callback, :token_usage_updated, %{session_id: event_id, usage: totals, payload: payload, provider: :claude})
      await_turn(session, request, event_id, issue, callback, until, pending)
    else
      pause(session, "protocol_error", %{})
    end
  end

  defp handle_message(%{"type" => "tool_call", "tool_id" => tool, "name" => name, "arguments" => args}, session, request, event_id, issue, callback, until, pending) do
    seen_key = {__MODULE__, session.id, :tools}
    seen = Process.get(seen_key, %{})

    if valid_tool_call?(session, seen, tool, name, args) do
      Process.put(seen_key, Map.put(seen, tool, true))

      result =
        ToolCall.run(fn -> Tracker.execute_bound_agent_tool(session.binding, name, args, issue: issue) end, name, args,
          issue_id: issue.id,
          issue_identifier: issue.identifier,
          session_id: event_id,
          tool_id: tool,
          provider: :claude,
          profile: session.identity["profile"]
        )

      {:ok, result, context} = result
      deliver_tool_result(session, request, event_id, issue, callback, until, pending, tool, result, context)
    else
      pause(session, "protocol_error", %{})
    end
  end

  defp handle_message(%{"type" => "tool_result_accepted", "tool_id" => tool}, session, request, event_id, issue, callback, until, pending) do
    if Map.has_key?(pending, tool) and ToolCall.accept(pending[tool]) == :ok do
      await_turn(session, request, event_id, issue, callback, until, Map.delete(pending, tool))
    else
      pause(session, "protocol_error", %{})
    end
  end

  defp handle_message(%{"type" => "turn_result", "outcome" => "completed"}, session, _request, event_id, _issue, _callback, _until, pending) do
    if map_size(pending) == 0 do
      {:ok, %{session_id: event_id, provider: :claude}}
    else
      pending_failure(session, pending)
    end
  end

  defp handle_message(%{"type" => "turn_result", "outcome" => outcome} = message, session, _request, _event_id, _issue, _callback, _until, pending) do
    cond do
      outcome == "protocol_error" ->
        case turn_failure(message["code"]) do
          nil -> pause(session, outcome, %{})
          cause -> pending_failure(session, pending, cause)
        end

      outcome in @pause_kinds ->
        pause(session, outcome, Map.take(message, ["resets_at"]))

      outcome in ["permission_blocked", "agent_turn_limit"] ->
        {:error, {:agent_blocked, outcome}}

      outcome == "transient_error" ->
        {:error, :claude_transient_error}

      outcome == "cancelled" ->
        {:error, :claude_cancelled}

      true ->
        pause(session, "protocol_error", %{})
    end
  end

  defp handle_message(_message, session, _request, _event_id, _issue, _callback, _until, _pending),
    do: pause(session, "protocol_error", %{})

  defp turn_failure("native_eof"), do: :claude_native_eof
  defp turn_failure("native_event_error"), do: :claude_native_event_error
  defp turn_failure("turn_timeout"), do: :claude_turn_timeout
  defp turn_failure(_code), do: nil

  # Validate categories again at the transport boundary; arbitrary helper text
  # and unknown details must never reach dashboards or logs.
  defp progress_summary(%{"status" => status} = message) when status in ["tool_started", "tool_running"] do
    tool =
      case message["tool"] do
        "command" -> "command"
        "read" -> "file or web read"
        "edit" -> "file edit"
        "search" -> "search"
        _ -> "tool"
      end

    prefix = if status == "tool_started", do: "Requested", else: "Waiting for"
    {"#{prefix} #{tool}", status == "tool_started"}
  end

  defp progress_summary(%{"status" => "response"}), do: {"Agent response received", true}
  defp progress_summary(%{"status" => "retrying"}), do: {"Waiting for retry", false}
  defp progress_summary(%{"status" => "compacting"}), do: {"Compacting context", false}
  defp progress_summary(_message), do: {"Waiting for agent activity", false}

  # credo:disable-for-next-line Credo.Check.Refactor.FunctionArity
  defp deliver_tool_result(session, request, event_id, issue, callback, until, pending, tool, result, context) do
    case send_tool_result(session, request, tool, result) do
      :ok -> await_turn(session, request, event_id, issue, callback, until, Map.put(pending, tool, context))
      _ -> tool_pause(session, ToolCall.fail(context, "reply_failed"))
    end
  end

  defp pending_failure(session, pending, cause \\ nil) do
    Enum.each(pending, fn {_id, context} -> ToolCall.fail(context, "ack_missing") end)
    if cause, do: {:error, cause}, else: pause(session, "protocol_error", %{})
  end

  defp valid_tool_call?(session, seen, tool, name, args) do
    is_binary(tool) and Enum.any?(session.binding.tool_specs, &(&1["name"] == name)) and
      not Map.has_key?(seen, tool) and map_size(seen) < 4096 and
      is_map(args) and byte_size(Jason.encode!(args)) <= 524_288
  end

  defp tool_pause(session, {:error, kind, _context}), do: pause(session, to_string(kind), %{})

  defp send_tool_result(session, request, tool, %{"success" => success, "output" => output}) do
    write(session.port, %{v: 1, type: "tool_result", session_id: session.id, request_id: request, tool_id: tool, result: %{success: success, output: output}})
  end

  @impl true
  def stop_session(session) do
    result = stop_owned_session(session)

    close_port(session.port)
    Process.delete({__MODULE__, session.id, :tools})
    result
  catch
    _, _ ->
      close_port(Map.get(session, :port))

      {:error, :cleanup_unconfirmed}
  end

  defp stop_owned_session(session) do
    if Port.info(session.port) && self() == session.owner do
      write(session.port, %{v: 1, type: "stop", session_id: session.id, request_id: new_id()})
      await_stop_or_cleanup(session)
    else
      cleanup_owned(session)
    end
  end

  defp await_stop_or_cleanup(session) do
    case await_stopped(session.port, deadline(5_000)) do
      :ok -> :ok
      _ -> cleanup_owned(session)
    end
  end

  @impl true
  def interrupt(session, _reason) do
    # This may be called by the scheduler while the Port owner is waiting on a
    # tracker request. Cleanup uses captured process generations, never a broad kill.
    send(session.owner, {:claude_session_interrupt, session.port})

    with :ok <- cleanup_owned(session), do: close_port(session.port)
  end

  defp cleanup_owned(%{owned_native: [], helper_identity: nil}), do: :ok

  defp cleanup_owned(session) do
    if File.regular?(session.cleanup) and is_binary(System.find_executable("timeout")) do
      case System.cmd(System.find_executable("timeout"), ["--signal=KILL", "10s", session.node, session.cleanup, Jason.encode!(%{helper: session.helper_identity, native: session.owned_native})],
             env: Config.claude_cleanup_environment(),
             stderr_to_stdout: true
           ) do
        {_output, 0} -> :ok
        _ -> {:error, :cleanup_unconfirmed}
      end
    else
      {:error, :cleanup_unconfirmed}
    end
  end

  defp await_stopped(port, until) do
    case frame(port, until) do
      {:ok, %{"type" => "stopped", "v" => 1}} -> :ok
      {:ok, _other} -> await_stopped(port, until)
      {:error, {:helper_exited, _status}} -> {:error, :unclean_helper_exit}
      error -> error
    end
  end

  defp pause(session, kind, details) do
    details = Map.put(details, "profile", session.identity["profile"])

    if kind in ["protocol_error", "tool_outcome_uncertain", "tracker_delivery_failed"] do
      {:error, {:agent_blocked, kind}}
    else
      AgentPause.record(kind, details)
      {:error, {:agent_pause, @codes[kind] || :policy_blocked, details}}
    end
  end

  defp paused_start(code) do
    kind = if code in ["auth_required", "unqualified_auth", "unknown_auth_status"], do: :auth_required, else: :policy_blocked
    details = %{startup_code: AgentPause.safe_startup_code(code), stage: "startup"}
    AgentPause.record(kind, details)
    {:error, {:agent_pause, kind, details}}
  end

  defp start_port(workspace, env, opts) do
    node = Keyword.get(opts, :node_path, Config.claude_node_path())
    helper = Keyword.get(opts, :helper_path, Config.claude_helper_path())

    if File.regular?(node) and File.regular?(helper) do
      port_env = Enum.map(env, fn {key, value} -> {String.to_charlist(key), if(is_nil(value), do: false, else: String.to_charlist(value))} end)
      {:ok, Port.open({:spawn_executable, String.to_charlist(node)}, [:binary, :exit_status, args: [String.to_charlist(helper)], cd: String.to_charlist(workspace), env: port_env, line: @max_frame])}
    else
      {:error, :claude_runtime_missing}
    end
  end

  defp frame(port, until, buffer \\ "") do
    wait = max(0, until - System.monotonic_time(:millisecond))

    receive do
      {:claude_session_interrupt, ^port} ->
        {:error, :interrupted}

      {^port, {:data, {:noeol, chunk}}} ->
        if byte_size(buffer) + byte_size(chunk) <= @max_frame do
          frame(port, until, buffer <> chunk)
        else
          {:error, :frame_too_large}
        end

      {^port, {:data, {:eol, chunk}}} ->
        if byte_size(buffer) + byte_size(chunk) <= @max_frame do
          case Jason.decode(buffer <> chunk) do
            {:ok, value} when is_map(value) -> {:ok, value}
            _ -> {:error, :invalid_frame}
          end
        else
          {:error, :frame_too_large}
        end

      {^port, {:exit_status, status}} ->
        {:error, {:helper_exited, status}}
    after
      wait -> {:error, :timeout}
    end
  end

  defp write(port, message) do
    data = Jason.encode!(message) <> "\n"

    if byte_size(data) <= @max_frame do
      try do
        Port.command(port, data)
        :ok
      rescue
        ArgumentError -> {:error, :port_closed}
      end
    else
      {:error, :frame_too_large}
    end
  end

  defp port_identity(port) do
    with {:os_pid, pid} <- Port.info(port, :os_pid),
         {:ok, data} <- File.read("/proc/#{pid}/stat"),
         [_, fields] <- Regex.run(~r/^\d+ \(.*\) (.*)$/s, String.trim(data)),
         start when is_binary(start) <- Enum.at(String.split(fields), 19) do
      %{pid: pid, start: start}
    else
      _ -> nil
    end
  end

  defp valid_owned?(owned) when is_list(owned) and length(owned) <= 1 do
    Enum.all?(owned, fn
      %{"pid" => pid, "start" => start} when is_integer(pid) and pid > 1 and is_binary(start) -> Regex.match?(~r/^\d+$/, start)
      _ -> false
    end)
  end

  defp valid_owned?(_owned), do: false
  defp valid_profile?(profile) when is_binary(profile), do: Regex.match?(~r/^[a-f0-9]{64}$/, profile)
  defp valid_profile?(_profile), do: false
  defp valid_usage?(totals) when is_map(totals), do: Enum.all?(~w(input_tokens output_tokens total_tokens), &(is_integer(totals[&1]) and totals[&1] >= 0))
  defp valid_usage?(_totals), do: false
  defp emit(callback, event, details), do: callback.(Map.merge(details, %{event: event, timestamp: DateTime.utc_now()}))
  defp deadline(ms), do: System.monotonic_time(:millisecond) + ms
  defp new_id, do: :crypto.strong_rand_bytes(16) |> Base.encode16(case: :lower)

  defp close_port(port) do
    try do
      Port.close(port)
    rescue
      ArgumentError -> :ok
    end

    :ok
  end
end
