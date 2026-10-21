defmodule SymphonyElixir.AgentReadiness do
  @moduledoc "Model-free native-runtime readiness."

  alias SymphonyElixir.Config

  @codes ~w(auth_required unqualified_auth unknown_auth_status unqualified_native_settings
            native_identity_mismatch
            runtime_version_mismatch sdk_version_mismatch invalid_tool_environment
            nonroot_profile_required container_profile_required runtime_unavailable)

  @spec check(keyword()) :: map()
  def check(opts \\ []) do
    if Config.coding_agent() == :codex do
      %{ready: true, provider: "codex", profile: SymphonyElixir.AgentPause.codex_profile()}
    else
      if File.exists?(Path.join(Config.agent_state_root(), "claude-cleanup-unconfirmed")) do
        blocked("cleanup_restart_required")
      else
        do_check(opts)
      end
    end
  end

  defp do_check(opts) do
    node = Keyword.get(opts, :node_path, Config.claude_node_path())
    helper = Keyword.get(opts, :helper_path, Config.claude_helper_path())

    with {:ok, env} <- Config.claude_child_environment(),
         true <- File.regular?(node) and File.regular?(helper),
         timeout when is_binary(timeout) <- System.find_executable("timeout"),
         duration when is_binary(duration) <- check_duration(opts),
         {data, status} when byte_size(data) <= 4096 <- System.cmd(timeout, ["--signal=KILL", duration, node, helper, "check"], env: env, stderr_to_stdout: true),
         {:ok, value} when is_map(value) <- Jason.decode(data) do
      interpret(status, value)
    else
      {:error, :invalid_tool_environment} -> blocked("invalid_tool_environment")
      _ -> blocked("runtime_unavailable")
    end
  rescue
    _ -> blocked("runtime_unavailable")
  end

  # GNU timeout owns and kills the complete model-free check process group. Port
  # closure alone does not terminate an external helper on scheduler/task exit.
  defp check_duration(opts) do
    case Keyword.get(opts, :timeout_ms, 35_000) do
      milliseconds when is_integer(milliseconds) and milliseconds > 0 ->
        :erlang.float_to_binary(min(milliseconds, 35_000) / 1_000, decimals: 3) <> "s"

      _ ->
        nil
    end
  end

  defp interpret(0, %{"ready" => true, "protocol" => 1, "profile" => profile, "sdk" => sdk, "cli" => cli, "policy" => 1})
       when is_binary(profile) and is_binary(sdk) and byte_size(sdk) in 1..128 and
              is_binary(cli) and byte_size(cli) in 1..128 do
    if Regex.match?(~r/^[a-f0-9]{64}$/, profile) do
      %{
        ready: true,
        provider: "claude",
        profile: profile,
        sdk: sdk,
        cli: cli,
        protocol: 1,
        policy: 1,
        local_policy_passed: true
      }
    else
      blocked("runtime_unavailable")
    end
  end

  defp interpret(_status, %{"code" => code}) when code in @codes, do: blocked(code)
  defp interpret(_status, _value), do: blocked("runtime_unavailable")

  defp blocked(code), do: %{ready: false, provider: "claude", code: code, local_policy_passed: false}
end
