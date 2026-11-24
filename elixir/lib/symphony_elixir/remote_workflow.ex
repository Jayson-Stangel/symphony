defmodule SymphonyElixir.RemoteWorkflow do
  @moduledoc "Reads an exact root workflow blob through the runner's project Git credential helper."

  @max_workflow_bytes 1_048_576
  @git_timeout "15"

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
    Regex.match?(~r{\Ahttps://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+\.git\z}, source.repository) and
      Regex.match?(~r/\A[a-zA-Z0-9][a-zA-Z0-9-]*\z/, source.project)
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
    case git(["ls-remote", "--symref", "--exit-code", "--", repository, "HEAD"]) do
      {output, 0} ->
        case Regex.run(~r/^ref: refs\/heads\/([^\t\n]+)\tHEAD$/m, output, capture: :all_but_first) do
          [branch] -> {:ok, branch}
          _ -> {:error, :missing_branch}
        end

      {_, 2} ->
        {:error, :missing_branch}

      _ ->
        {:error, :unreachable}
    end
  end

  defp check_branch(branch) do
    case git(["check-ref-format", "refs/heads/" <> branch]) do
      {_, 0} -> :ok
      _ -> {:error, :invalid_branch}
    end
  end

  defp ref_commit(repository, ref) do
    case git(["ls-remote", "--refs", "--exit-code", "--", repository, ref]) do
      {output, 0} ->
        commits =
          for line <- String.split(output, "\n"),
              [commit, ^ref] <- [String.split(line, "\t")],
              do: commit

        case commits do
          [commit] -> validate_commit(commit)
          [] -> {:error, :missing_branch}
          _ -> {:error, :unreachable}
        end

      {_, 2} ->
        {:error, :missing_branch}

      _ ->
        {:error, :unreachable}
    end
  end

  defp fetch_blob(repository, branch, ref, scratch_root) do
    directory = Path.join(scratch_root, "symphony-workflow-" <> Base.url_encode64(:crypto.strong_rand_bytes(18), padding: false))

    case make_scratch(scratch_root, directory) do
      :ok ->
        try do
          with {_, 0} <- git(["init", "--bare", "--quiet", directory]),
               {_, 0} <- git(["--git-dir", directory, "remote", "add", "origin", repository]),
               {_, 0} <- git(["--git-dir", directory, "fetch", "--quiet", "--no-tags", "--depth=1", "--filter=blob:none", "origin", ref]),
               {raw_commit, 0} <- git(["--git-dir", directory, "rev-parse", "--verify", "FETCH_HEAD^{commit}"]),
               {:ok, commit} <- validate_commit(String.trim(raw_commit)),
               {:ok, content} <- read_blob(directory, commit) do
            {:ok, %{branch: branch, commit: commit, content: content}}
          else
            {:error, reason} -> {:error, reason}
            _ -> {:error, :unreachable}
          end
        after
          File.rm_rf(directory)
        end

      {:error, _reason} ->
        {:error, :unreachable}
    end
  end

  defp make_scratch(root, directory) do
    with :ok <- File.mkdir_p(root),
         {:ok, %File.Stat{type: :directory}} <- File.lstat(root) do
      File.mkdir(directory)
    else
      _ -> {:error, :invalid_scratch}
    end
  end

  defp read_blob(directory, commit) do
    object = commit <> ":WORKFLOW.md"

    case git(["--git-dir", directory, "ls-tree", commit, "--", "WORKFLOW.md"]) do
      {"", 0} ->
        {:error, :missing_workflow}

      {<<mode::binary-size(6), " blob ", _::binary>>, 0} when mode in ["100644", "100755"] ->
        with {size, 0} <- git(["--git-dir", directory, "cat-file", "-s", object]),
             {bytes, ""} when bytes <= @max_workflow_bytes <- Integer.parse(String.trim(size)),
             {content, 0} <- git(["--git-dir", directory, "cat-file", "blob", object]),
             true <- byte_size(content) == bytes and String.valid?(content) do
          {:ok, content}
        else
          {_, status} when is_integer(status) -> {:error, :unreachable}
          _ -> {:error, :invalid_workflow}
        end

      {_, 0} ->
        {:error, :invalid_workflow}

      _ ->
        {:error, :unreachable}
    end
  end

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
  rescue
    ErlangError -> {"", 1}
  end
end
