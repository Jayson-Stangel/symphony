FROM elixir:1.19@sha256:db1cbca0b49e26e65dd804c75f27ec5f01835f1d29f8b8e4cc8a51f01f986f9f AS browser-tools

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git nodejs npm python3 ripgrep \
  && install -d -m 755 /etc/apt/keyrings \
  && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && chmod 644 /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && printf 'deb [arch=%s signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\n' \
    "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/github-cli.list \
  && apt-get update && apt-get install -y --no-install-recommends gh \
  && gh --version \
  && rm -rf /var/lib/apt/lists/*

# Keep browser tools readable by isolated browser test users.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/symphony/ms-playwright
WORKDIR /opt/symphony/browser
COPY runner/browser/package.json runner/browser/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund \
  && ./node_modules/.bin/playwright install --with-deps chromium \
  && ln -s /opt/symphony/browser/node_modules/.bin/playwright /usr/local/bin/playwright \
  && chmod -R a+rX /opt/symphony/browser /opt/symphony/ms-playwright \
  && rm -rf /var/lib/apt/lists/* \
  && npm cache clean --force
COPY runner/browser/smoke.cjs ./
RUN node smoke.cjs

FROM browser-tools AS codex
LABEL com.symphony.agent=codex
RUN npm install --global @openai/codex@latest

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
