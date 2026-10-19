defmodule SymphonyElixir.AgentReadiness do
  @moduledoc "Model-free native-runtime readiness. Account billing remains operator attestation."

  alias SymphonyElixir.Config

  @codes ~w(auth_required unqualified_auth unknown_auth_status unqualified_native_settings
            overage_attestation_required overage_attestation_expired native_identity_mismatch
            runtime_version_mismatch sdk_version_mismatch conflicting_inference_environment
            nonroot_profile_required container_profile_required runtime_unavailable)

  @spec check(keyword()) :: map()
  def check(opts \\ []) do
    if Config.coding_agent() == :codex do
      %{ready: true, provider: "codex"}
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
         {data, status} when byte_size(data) <= 4096 <- System.cmd(node, [helper, "check"], env: env, stderr_to_stdout: true),
         {:ok, value} when is_map(value) <- Jason.decode(data) do
      interpret(status, value)
    else
      {:error, :conflicting_inference_environment} -> blocked("conflicting_inference_environment")
      _ -> blocked("runtime_unavailable")
    end
  rescue
    _ -> blocked("runtime_unavailable")
  end

  defp interpret(0, %{"ready" => true, "profile" => profile, "sdk" => "0.3.303", "cli" => "2.1.303", "policy" => 1, "overages_disabled_attested" => true, "attested_at" => at})
       when is_binary(profile) and is_binary(at) do
    if Regex.match?(~r/^[a-f0-9]{64}$/, profile) and match?({:ok, _, _}, DateTime.from_iso8601(at)) do
      %{
        ready: true,
        provider: "claude",
        profile: profile,
        sdk: "0.3.303",
        cli: "2.1.303",
        policy: 1,
        local_policy_passed: true,
        overages_disabled_attested: true,
        attested_at: at
      }
    else
      blocked("runtime_unavailable")
    end
  end

  defp interpret(_status, %{"code" => code}) when code in @codes, do: blocked(code)
  defp interpret(_status, _value), do: blocked("runtime_unavailable")

  defp blocked(code), do: %{ready: false, provider: "claude", code: code, local_policy_passed: false}
end
