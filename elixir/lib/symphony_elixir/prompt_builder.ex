defmodule SymphonyElixir.PromptBuilder do
  @moduledoc """
  Builds agent prompts from normalized tracker work item data.
  """

  alias SymphonyElixir.{AgentPause, Config, Workflow}
  alias SymphonyElixir.Tracker.Issue

  @render_opts [strict_variables: true, strict_filters: true]

  @spec build_prompt(SymphonyElixir.Tracker.Issue.t(), keyword()) :: String.t()
  def build_prompt(issue, opts \\ []) do
    template =
      Workflow.current()
      |> prompt_template!()
      |> parse_template!()

    template
    |> Solid.render!(render_values(issue, Keyword.get(opts, :attempt)), @render_opts)
    |> IO.iodata_to_binary()
    |> Kernel.<>(tracker_recovery_instructions(issue.id))
  end

  @doc "Checks a template with the worker's strict rendering rules and representative issue inputs."
  @spec validate_template(String.t()) :: :ok | {:error, :invalid_workflow}
  def validate_template(prompt) do
    heap = %{size: 2_000_000, kill: true, error_logger: false, include_shared_binaries: true}

    {pid, ref} =
      :erlang.spawn_opt(fn -> exit(validate_render(prompt)) end, [:monitor, {:max_heap_size, heap}])

    {:ok, timer} = :timer.kill_after(2_000, pid)

    result =
      receive do
        {:DOWN, ^ref, :process, ^pid, :ok} -> :ok
        {:DOWN, ^ref, :process, ^pid, _reason} -> {:error, :invalid_workflow}
      end

    :timer.cancel(timer)
    result
  end

  defp validate_render(prompt) do
    issue = %Issue{
      id: "fixture",
      identifier: "FIXTURE-1",
      title: "Example issue",
      description: "Example description",
      priority: 1,
      state: "In Progress",
      branch_name: "fixture-branch",
      url: "https://example.invalid",
      labels: ["fixture"],
      blocked_by: [%{id: "blocked", identifier: "FIXTURE-2", state: "Done"}]
    }

    with {:ok, template} <- Solid.parse(prompt),
         true <- Enum.all?([nil, 1, 2], &match?({:ok, _, _}, Solid.render(template, render_values(issue, &1), @render_opts))) do
      :ok
    else
      _ -> {:error, :invalid_workflow}
    end
  end

  defp render_values(issue, attempt), do: %{"attempt" => attempt, "issue" => issue |> Map.from_struct() |> to_solid_map()}

  defp tracker_recovery_instructions(issue_id) do
    """

    ## Tracker recovery

    You own tracker error recovery for this issue. Read the current issue, workpad,
    comments, attachments and relevant external state at the start of each attempt.
    Tool failure does not prove that a write was rolled back, and delivery of a
    response does not prove the intended change succeeded. After a failed,
    interrupted or uncertain write, read the target before retrying. If the intended
    change already exists, continue without duplicating it. Correct rejected
    requests using the error and schema; fetch exact team state UUIDs before state
    changes. Do not repeat an unchanged failing request or blindly replay comment,
    attachment or other create mutations. Use bounded read/correction attempts.
    For partial or multi-object mutations, reconcile each affected target; do not
    replay the entire mutation. If the outcome remains unresolved, avoid that
    write and dependent writes, record the failure and evidence in the available
    workpad (locally if tracker access failed), and continue safe independent
    implementation or verification. Block only this issue through the workflow
    when its remaining required work depends on the unresolved outcome, then stop.
    Never clear runner state or change credentials to recover a tracker write.
    """ <> AgentPause.recovery_notice(issue_id)
  end

  defp prompt_template!({:ok, %{prompt_template: prompt}}), do: default_prompt(prompt)

  defp prompt_template!({:error, reason}) do
    raise RuntimeError, "workflow_unavailable: #{inspect(reason)}"
  end

  defp parse_template!(prompt) when is_binary(prompt) do
    Solid.parse!(prompt)
  rescue
    error ->
      reraise %RuntimeError{
                message: "template_parse_error: #{Exception.message(error)} template=#{inspect(prompt)}"
              },
              __STACKTRACE__
  end

  defp to_solid_map(map) when is_map(map) do
    Map.new(map, fn {key, value} -> {to_string(key), to_solid_value(value)} end)
  end

  defp to_solid_value(%DateTime{} = value), do: DateTime.to_iso8601(value)
  defp to_solid_value(%NaiveDateTime{} = value), do: NaiveDateTime.to_iso8601(value)
  defp to_solid_value(%Date{} = value), do: Date.to_iso8601(value)
  defp to_solid_value(%Time{} = value), do: Time.to_iso8601(value)
  defp to_solid_value(%_{} = value), do: value |> Map.from_struct() |> to_solid_map()
  defp to_solid_value(value) when is_map(value), do: to_solid_map(value)
  defp to_solid_value(value) when is_list(value), do: Enum.map(value, &to_solid_value/1)
  defp to_solid_value(value), do: value

  defp default_prompt(prompt) when is_binary(prompt) do
    if String.trim(prompt) == "" do
      Config.workflow_prompt()
    else
      prompt
    end
  end
end
