defmodule SymphonyElixir.PromptBuilder do
  @moduledoc """
  Builds agent prompts from normalized tracker work item data.
  """

  alias SymphonyElixir.{AgentPause, Config, Workflow}

  @render_opts [strict_variables: true, strict_filters: true]

  @spec build_prompt(SymphonyElixir.Tracker.Issue.t(), keyword()) :: String.t()
  def build_prompt(issue, opts \\ []) do
    template =
      Workflow.current()
      |> prompt_template!()
      |> parse_template!()

    template
    |> Solid.render!(
      %{
        "attempt" => Keyword.get(opts, :attempt),
        "issue" => issue |> Map.from_struct() |> to_solid_map()
      },
      @render_opts
    )
    |> IO.iodata_to_binary()
    |> Kernel.<>(tracker_recovery_instructions(issue.id))
  end

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
