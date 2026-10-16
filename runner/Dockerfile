FROM elixir:1.19

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git gh nodejs npm python3 ripgrep \
  && rm -rf /var/lib/apt/lists/* \
  && npm install --global @openai/codex@latest

WORKDIR /opt/symphony/elixir
COPY elixir/mix.exs elixir/mix.lock ./
RUN mix local.hex --force && mix local.rebar --force && MIX_ENV=prod mix deps.get --only prod
COPY elixir ./
RUN MIX_ENV=prod mix escript.build

COPY .codex/skills /opt/symphony/skills
RUN install -d -m 700 /root/.codex /workspaces /var/lib/symphony
COPY runner/entrypoint.sh /usr/local/bin/symphony-runner
RUN chmod 755 /usr/local/bin/symphony-runner

EXPOSE 4010

ENTRYPOINT ["/usr/local/bin/symphony-runner"]
