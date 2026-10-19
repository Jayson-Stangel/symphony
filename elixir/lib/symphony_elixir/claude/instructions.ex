defmodule SymphonyElixir.Claude.Instructions do
  @moduledoc false

  alias SymphonyElixir.PathSafety

  @guidance """
  Follow the repository's root and directory AGENTS.md and CLAUDE.md instructions.
  Before working in a subdirectory, read the applicable instruction files between
  the repository root and that directory. Preserve both sets of constraints; stop
  for operator input if they cannot be reconciled. Do not create or overwrite
  CLAUDE.md, AGENTS.md, or tracked setup files merely to configure this runtime.
  Symphony owns scheduling, authentication, billing policy and tracker bindings.
  Do not replace native credentials, enable paid features, or choose another
  inference route. Use the supplied tracker tool aliases and canonical Symphony
  skills. Reuse an existing Codex Workpad or Symphony Workpad; keep one workpad.
  """

  @spec context(Path.t()) :: {:ok, String.t()} | {:error, atom()}
  def context(workspace) do
    with {:ok, agents} <- read_instruction(workspace, "AGENTS.md"),
         {:ok, claude} <- read_instruction(workspace, "CLAUDE.md") do
      text = @guidance <> "\n## Root AGENTS.md\n" <> agents <> "\n## Root CLAUDE.md\n" <> claude
      if byte_size(text) <= 131_072, do: {:ok, text}, else: {:error, :repository_instructions_too_large}
    end
  end

  defp read_instruction(workspace, name) do
    path = Path.join(workspace, name)

    case File.lstat(path) do
      {:error, :enoent} ->
        {:ok, ""}

      {:ok, _stat} ->
        with {:ok, canonical} <- PathSafety.canonicalize(path),
             true <- String.starts_with?(canonical, workspace <> "/"),
             {:ok, %{type: :regular, size: size}} <- File.stat(canonical),
             true <- size <= 131_072,
             {:ok, content} <- File.read(canonical),
             true <- String.valid?(content) do
          {:ok, content}
        else
          _ -> {:error, :unsafe_repository_instructions}
        end

      _ ->
        {:error, :unreadable_repository_instructions}
    end
  end
end
