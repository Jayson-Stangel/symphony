defmodule SymphonyElixir.RemoteWorkflow do
  @moduledoc "Reads an exact root workflow blob through the runner's project Git credential helper."

  @max_workflow_bytes 1_048_576
  @git_timeout "15"
  @github_repository ~r{\A(https://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+)\.git\z}

  @type source :: %{optional(:scratch_root) => Path.t(), repository: binary(), branch: binary(), project: binary()}
  @type candidate :: %{branch: String.t(), commit: String.t(), content: binary()}
  @type source_error :: :unreachable | :missing_branch | :invalid_branch | :missing_workflow | :invalid_workflow

  @spec source_from_environment() :: source() | nil
  def source_from_environment do
    case System.get_env("SYMPHONY_REMOTE_REPOSITORY") do
      repository when repository not in [nil, ""] ->
        %{
          repository: repository,
          branch: System.get_env("SYMPHONY_REMOTE_BRANCH", ""),
          project: System.get_env("SYMPHONY_REMOTE_LINEAR_PROJECT", "")
        }

      _ ->
        nil
    end
  end

  @spec accepted_path() :: Path.t()
  def accepted_path do
    Path.join(SymphonyElixir.Config.agent_state_root(), "remote-workflow/WORKFLOW.md")
  end

  @spec valid_source?(source()) :: boolean()
  def valid_source?(source) do
    Regex.match?(@github_repository, source.repository) and
      Regex.match?(~r/\A[a-zA-Z0-9][a-zA-Z0-9-]*\z/, source.project)
  end

  @doc "Returns a GitHub link to the root workflow at the accepted commit."
  @spec github_workflow_url(String.t(), String.t()) :: String.t() | nil
  def github_workflow_url(repository, commit) do
    with [github_url] <- Regex.run(@github_repository, repository, capture: :all_but_first),
         {:ok, commit} <- validate_commit(commit) do
      github_url <> "/blob/" <> commit <> "/WORKFLOW.md"
    else
      _ -> nil
    end
  end

  @spec fetch(source(), String.t() | nil) :: {:ok, candidate() | :unchanged} | {:error, source_error()}
  def fetch(source, accepted_commit \\ nil) do
    with {:ok, branch} <- select_branch(source),
         :ok <- check_branch(branch),
         ref = "refs/heads/" <> branch,
         {:ok, commit} <- ref_commit(source.repository, ref) do
      if commit == accepted_commit do
        {:ok, :unchanged}
      else
        fetch_blob(source.repository, branch, ref, Map.get(source, :scratch_root, "/tmp"))
      end
    end
  end

  defp select_branch(%{branch: branch}) when branch != "", do: {:ok, branch}

  defp select_branch(%{repository: repository}) do
    with {:ok, output} <- remote_refs(["--symref", "--exit-code", "--", repository, "HEAD"]),
         [branch] <- Regex.run(~r/^ref: refs\/heads\/([^\t\n]+)\tHEAD$/m, output, capture: :all_but_first) do
      {:ok, branch}
    else
      nil -> {:error, :missing_branch}
      {:error, reason} -> {:error, reason}
    end
  end

  defp check_branch(branch) do
    case git(["check-ref-format", "refs/heads/" <> branch]) do
      {_, 0} -> :ok
      _ -> {:error, :invalid_branch}
    end
  end

  defp ref_commit(repository, ref) do
    with {:ok, output} <- remote_refs(["--refs", "--exit-code", "--", repository, ref]) do
      commit =
        output
        |> String.split("\n")
        |> Enum.find_value(&ref_value(&1, ref))

      if commit, do: validate_commit(commit), else: {:error, :missing_branch}
    end
  end

  defp ref_value(line, ref) do
    case String.split(line, "\t") do
      [commit, ^ref] -> commit
      _ -> nil
    end
  end

  defp remote_refs(args) do
    case git(["ls-remote" | args]) do
      {output, 0} -> {:ok, output}
      {_, 2} -> {:error, :missing_branch}
      result -> git_output(result)
    end
  end

  defp fetch_blob(repository, branch, ref, scratch_root) do
    directory = Path.join(scratch_root, "symphony-workflow-" <> Base.url_encode64(:crypto.strong_rand_bytes(18), padding: false))
    git_dir = ["--git-dir", directory]

    with :ok <- make_scratch(scratch_root, directory) do
      try do
        with {:ok, _} <- command_output(["init", "--bare", "--quiet", directory]),
             {:ok, _} <- command_output(git_dir ++ ["remote", "add", "origin", repository]),
             {:ok, _} <- command_output(git_dir ++ ["fetch", "--quiet", "--no-tags", "--depth=1", "--filter=blob:none", "origin", ref]),
             {:ok, raw_commit} <- command_output(git_dir ++ ["rev-parse", "--verify", "FETCH_HEAD^{commit}"]),
             {:ok, commit} <- validate_commit(String.trim(raw_commit)),
             {:ok, tree} <- command_output(git_dir ++ ["ls-tree", commit, "--", "WORKFLOW.md"]),
             {:ok, content} <- read_blob(tree, git_dir, commit <> ":WORKFLOW.md") do
          {:ok, %{branch: branch, commit: commit, content: content}}
        end
      after
        File.rm_rf(directory)
      end
    end
  end

  defp make_scratch(root, directory) do
    with :ok <- File.mkdir_p(root),
         {:ok, %File.Stat{type: :directory}} <- File.lstat(root),
         :ok <- File.mkdir(directory) do
      :ok
    else
      _ -> {:error, :unreachable}
    end
  end

  defp read_blob("", _git_dir, _object), do: {:error, :missing_workflow}

  defp read_blob(<<mode::binary-size(6), " blob ", _::binary>>, git_dir, object) when mode in ["100644", "100755"] do
    with {:ok, size} <- command_output(git_dir ++ ["cat-file", "-s", object]),
         {bytes, ""} when bytes <= @max_workflow_bytes <- Integer.parse(String.trim(size)),
         {:ok, content} <- command_output(git_dir ++ ["cat-file", "blob", object]),
         true <- byte_size(content) == bytes and String.valid?(content) do
      {:ok, content}
    else
      {:error, reason} -> {:error, reason}
      _ -> {:error, :invalid_workflow}
    end
  end

  defp read_blob(_tree, _git_dir, _object), do: {:error, :invalid_workflow}

  defp command_output(args), do: args |> git() |> git_output()

  defp git_output({value, 0}), do: {:ok, value}
  defp git_output({_value, _status}), do: {:error, :unreachable}

  defp validate_commit(commit) do
    if Regex.match?(~r/\A(?:[0-9a-f]{40}|[0-9a-f]{64})\z/, commit),
      do: {:ok, commit},
      else: {:error, :unreachable}
  end

  defp git(args) do
    # The product entrypoint accepts only canonical GitHub HTTPS URLs. The
    # lower-level reader also supports local remotes for credential-free tests.
    env =
      System.get_env()
      |> Map.keys()
      |> Map.new(&{&1, nil})
      |> Map.merge(%{
        "PATH" => "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM" => "1",
        "GIT_CONFIG_GLOBAL" => "/dev/null",
        "GIT_TERMINAL_PROMPT" => "0",
        "SYMPHONY_GIT_TOKEN" => System.get_env("SYMPHONY_GIT_TOKEN")
      })

    options = [
      "-c",
      "credential.helper=",
      "-c",
      "credential.helper=/usr/local/bin/symphony-git-credential",
      "-c",
      "http.followRedirects=false",
      "-c",
      "protocol.allow=never",
      "-c",
      "protocol.https.allow=always",
      "-c",
      "protocol.file.allow=always"
    ]

    System.cmd("/usr/bin/timeout", ["--kill-after=2", @git_timeout, "/usr/bin/git" | options ++ args],
      cd: "/",
      env: Map.to_list(env),
      stderr_to_stdout: true
    )
  end
end
