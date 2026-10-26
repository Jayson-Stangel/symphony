defmodule SymphonyElixir.Codex.ConfigSnapshot do
  @moduledoc "Bounded export of the original native trust-only Codex configuration."

  @boundary_resource Path.expand("../../../priv/codex_config_boundary.py", __DIR__)
  @snapshot_resource Path.expand("../../../priv/codex_config_snapshot.py", __DIR__)
  @external_resource @boundary_resource
  @external_resource @snapshot_resource
  @program File.read!(@boundary_resource) <> "\n" <> File.read!(@snapshot_resource)
  @refusal "sandbox_unqualified_config"

  @doc "Read only the fixed native config source without starting the application."
  @spec run(keyword()) :: {:ok, map()} | {:error, String.t()}
  def run(opts \\ []) do
    execute = Keyword.get(opts, :execute, &System.cmd/3)
    python = Keyword.get_lazy(opts, :python_path, fn -> System.find_executable("python3") end)

    with path when is_binary(path) <- python,
         {output, 0} when byte_size(output) <= 90_000 <- execute.(path, ["-I", "-c", @program], []),
         {:ok, %{"schema" => 1, "mode" => "codex-config-snapshot", "profile" => "trust-ro-v2"} = value} <-
           Jason.decode(output) do
      {:ok, value}
    else
      _ -> {:error, @refusal}
    end
  rescue
    _ -> {:error, @refusal}
  end
end
