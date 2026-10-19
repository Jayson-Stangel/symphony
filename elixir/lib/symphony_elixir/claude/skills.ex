defmodule SymphonyElixir.Claude.Skills do
  @moduledoc "Resolves canonical procedures and declared repository overrides without editing the checkout."

  alias SymphonyElixir.PathSafety

  @names ~w(symphony-commit symphony-push symphony-pull symphony-linear symphony-land)

  @spec sources(Path.t()) :: {:ok, map()} | {:error, atom()}
  def sources(workspace) do
    with {:ok, overrides} <- manifest(workspace) do
      Enum.reduce_while(@names, {:ok, %{}}, fn name, {:ok, sources} ->
        default = ".codex/skills/#{name}"
        source = Map.get(overrides, name, default)

        add_source(sources, workspace, source, name, Map.has_key?(overrides, name))
      end)
    end
  end

  defp add_source(sources, workspace, source, name, declared) do
    case resolve(workspace, source) do
      {:ok, nil} when declared and not is_nil(source) -> {:halt, {:error, :missing_skill_override}}
      {:ok, nil} -> {:cont, {:ok, sources}}
      {:ok, path} -> {:cont, {:ok, Map.put(sources, name, path)}}
      _ -> {:halt, {:error, :unsafe_skill_override}}
    end
  end

  defp manifest(workspace) do
    path = Path.join(workspace, ".codex/symphony-skill-overrides.yml")

    case File.lstat(path) do
      {:error, :enoent} ->
        {:ok, %{}}

      {:ok, %{type: :regular, size: size}} when size <= 32_768 ->
        with {:ok, data} <- File.read(path),
             {:ok, %{"overrides" => entries} = value} <- YamlElixir.read_from_string(data),
             true <- Map.keys(value) == ["overrides"] and is_list(entries) and length(entries) <= 5 do
          parse_entries(entries)
        else
          _ -> {:error, :invalid_skill_manifest}
        end

      _ ->
        {:error, :invalid_skill_manifest}
    end
  end

  defp parse_entries(entries) do
    Enum.reduce_while(entries, {:ok, %{}}, fn entry, {:ok, acc} ->
      with %{"name" => name, "source" => source} <- entry,
           true <- name in @names and not Map.has_key?(acc, name),
           true <- Enum.all?(Map.keys(entry), &(&1 in ["name", "source", "source_revision"])),
           {:ok, path} <- source_path(source) do
        {:cont, {:ok, Map.put(acc, name, path)}}
      else
        _ -> {:halt, {:error, :invalid_skill_manifest}}
      end
    end)
  end

  defp source_path("global"), do: {:ok, nil}

  defp source_path("legacy:" <> path) do
    if Regex.match?(~r/^\.codex\/skills\/[a-z][a-z0-9_-]*$/, path), do: {:ok, path}, else: {:error, :unsafe_skill_override}
  end

  defp source_path(_), do: {:error, :invalid_skill_manifest}

  defp resolve(_workspace, nil), do: {:ok, nil}

  defp resolve(workspace, relative) do
    path = Path.join(workspace, relative)

    case File.lstat(path) do
      {:error, :enoent} ->
        if String.starts_with?(relative, ".codex/skills/symphony-"), do: {:ok, nil}, else: {:error, :missing_skill_override}

      {:ok, %{type: :directory}} ->
        with {:ok, canonical} <- PathSafety.canonicalize(path),
             true <- String.starts_with?(canonical, workspace <> "/.codex/skills/"),
             true <- File.regular?(Path.join(canonical, "SKILL.md")) do
          {:ok, canonical}
        else
          _ -> {:error, :unsafe_skill_override}
        end

      _ ->
        {:error, :unsafe_skill_override}
    end
  end
end
