defmodule SymphonyElixirWeb.ObservabilityApiController do
  @moduledoc """
  JSON API for Symphony observability data.
  """

  use Phoenix.Controller, formats: [:json]

  alias Plug.Conn
  alias SymphonyElixirWeb.{Endpoint, Presenter}

  @spec state(Conn.t(), map()) :: Conn.t()
  def state(conn, _params) do
    json(conn, Presenter.state_payload(orchestrator(), snapshot_timeout_ms()))
  end

  @spec issue(Conn.t(), map()) :: Conn.t()
  def issue(conn, %{"issue_identifier" => issue_identifier}) do
    case Presenter.issue_payload(issue_identifier, orchestrator(), snapshot_timeout_ms()) do
      {:ok, payload} ->
        json(conn, payload)

      {:error, :issue_not_found} ->
        error_response(conn, 404, "issue_not_found", "Issue not found")
    end
  end

  @spec refresh(Conn.t(), map()) :: Conn.t()
  def refresh(conn, _params) do
    case Presenter.refresh_payload(orchestrator()) do
      {:ok, payload} ->
        conn
        |> put_status(202)
        |> json(payload)

      {:error, :unavailable} ->
        error_response(conn, 503, "orchestrator_unavailable", "Orchestrator is unavailable")
    end
  end

  @spec agent_control(Conn.t(), map()) :: Conn.t()
  def agent_control(conn, %{"action" => action} = params) do
    actions = %{"drain" => :drain, "resume" => :resume, "recheck" => :recheck}

    if get_req_header(conn, "x-symphony-control") == ["1"] and Map.has_key?(actions, action) do
      # Recheck may await one existing bounded observation before its own fresh check.
      timeout = if action == "recheck", do: 75_000, else: 40_000

      case GenServer.call(orchestrator(), {:agent_control, actions[action], params["confirm_tools_reconciled"] == true}, timeout) do
        {:ok, value} -> json(conn, value)
        {:error, reason} -> error_response(conn, 409, to_string(reason), "Agent control refused")
      end
    else
      error_response(conn, 403, "control_refused", "Explicit local control header and supported action required")
    end
  catch
    :exit, _ -> error_response(conn, 503, "orchestrator_unavailable", "Orchestrator is unavailable")
  end

  @spec method_not_allowed(Conn.t(), map()) :: Conn.t()
  def method_not_allowed(conn, _params) do
    error_response(conn, 405, "method_not_allowed", "Method not allowed")
  end

  @spec not_found(Conn.t(), map()) :: Conn.t()
  def not_found(conn, _params) do
    error_response(conn, 404, "not_found", "Route not found")
  end

  defp error_response(conn, status, code, message) do
    conn
    |> put_status(status)
    |> json(%{error: %{code: code, message: message}})
  end

  defp orchestrator do
    Endpoint.config(:orchestrator) || SymphonyElixir.Orchestrator
  end

  defp snapshot_timeout_ms do
    Endpoint.config(:snapshot_timeout_ms) || 15_000
  end
end
