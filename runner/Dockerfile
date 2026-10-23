# Keep this multi-platform Node pin aligned with the live E2E and qualification images.
FROM node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS node-runtime

FROM node-runtime AS claude-deps
WORKDIR /opt/symphony/claude
COPY runner/claude/package.json runner/claude/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY runner/claude/*.mjs ./

FROM elixir:1.19@sha256:db1cbca0b49e26e65dd804c75f27ec5f01835f1d29f8b8e4cc8a51f01f986f9f AS browser-tools

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git python3 ripgrep \
  && install -d -m 755 /etc/apt/keyrings \
  && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && chmod 644 /etc/apt/keyrings/githubcli-archive-keyring.gpg \
  && printf 'deb [arch=%s signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\n' \
    "$(dpkg --print-architecture)" > /etc/apt/sources.list.d/github-cli.list \
  && apt-get update && apt-get install -y --no-install-recommends gh \
  && gh --version \
  && rm -rf /var/lib/apt/lists/*

# Install Node and its matching npm together, without distro Node/npm or Yarn.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
  && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Keep browser binaries shared across the root Codex and non-root Claude users.
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

FROM browser-tools AS common
WORKDIR /opt/symphony/elixir
COPY elixir/mix.exs elixir/mix.lock ./
RUN mix local.hex --force && mix local.rebar --force && MIX_ENV=prod mix deps.get --only prod \
  && MIX_ENV=prod mix deps.compile
COPY elixir ./
RUN MIX_ENV=prod mix escript.build

COPY .codex/skills /opt/symphony/skills
RUN install -d -m 700 /root/.codex /workspaces /var/lib/symphony
COPY runner/entrypoint.sh /usr/local/bin/symphony-runner
RUN chmod 755 /usr/local/bin/symphony-runner

EXPOSE 4010

ENTRYPOINT ["/usr/local/bin/symphony-runner"]

FROM common AS claude
LABEL com.symphony.agent=claude com.symphony.protocol=1
COPY --from=claude-deps /opt/symphony/claude /opt/symphony/claude
RUN apt-get update && apt-get install -y --no-install-recommends tini \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd --gid 11001 claude \
  && useradd --uid 11001 --gid 11001 --home-dir /home/claude claude \
  && install -d -o claude -g claude -m 700 /home/claude /workspaces /var/lib/symphony \
  && install -d /opt/symphony/claude/plugin/.claude-plugin /opt/symphony/claude/plugin/skills \
  && install -d -m 755 /etc/symphony/claude \
  && printf '{"name":"symphony","version":"1.0.0","description":"Canonical Symphony procedures"}\n' \
    > /opt/symphony/claude/plugin/.claude-plugin/plugin.json \
  && for skill in commit push pull linear land; do \
       ln -s "/opt/symphony/skills/$skill" "/opt/symphony/claude/plugin/skills/symphony-$skill"; \
     done \
  && chmod -R a-w /opt/symphony/claude /opt/symphony/skills
COPY runner/claude/entrypoint.sh /usr/local/bin/symphony-claude-runner
COPY runner/claude/git-credential.sh /usr/local/bin/symphony-git-credential
RUN chmod 755 /usr/local/bin/symphony-claude-runner /usr/local/bin/symphony-git-credential
ENV HOME=/home/claude CLAUDE_CONFIG_DIR=/home/claude/.claude \
    CLAUDE_CODE_DISABLE_FAST_MODE=1 DISABLE_AUTOUPDATER=1 SYMPHONY_CONTAINERIZED=1
USER 11001:11001
WORKDIR /var/lib/symphony
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/symphony-claude-runner"]

# Identity belongs to the finished image, after every dependency/application layer.
ARG SYMPHONY_VERSION=development
ARG SYMPHONY_REVISION=unknown
ARG SYMPHONY_INPUT_IDENTITY=unknown
ARG TARGETARCH
LABEL org.opencontainers.image.version=$SYMPHONY_VERSION \
      org.opencontainers.image.revision=$SYMPHONY_REVISION \
      com.symphony.architecture=$TARGETARCH \
      com.symphony.inputs.sha256=$SYMPHONY_INPUT_IDENTITY

# Credential-free Linux verification of the same transport and process ownership.
FROM claude AS claude-test
USER 0:0
COPY . /opt/symphony
ENV MIX_HOME=/opt/symphony/test-mix HEX_HOME=/opt/symphony/test-hex
WORKDIR /opt/symphony/elixir
RUN mix local.hex --force && mix local.rebar --force && MIX_ENV=test mix deps.get \
  && MIX_ENV=test mix deps.compile \
  && chown -R 11001:11001 /opt/symphony /home/claude
COPY --chown=11001:11001 --from=claude-deps /opt/symphony/claude/node_modules /opt/symphony/runner/claude/node_modules
USER 11001:11001
ENTRYPOINT ["mix"]
CMD ["test", "--cover"]

# The default target retains Codex's existing root/auth layout.
FROM common AS codex
LABEL com.symphony.agent=codex com.symphony.protocol=1
ARG CODEX_VERSION=0.163.0
# The bundled helper matches this Codex release's proc-fallback diagnostics.
# System Bubblewrap 0.12 reports a different path and prevents that fallback.
RUN apt-get purge -y bubblewrap && npm install --global "@openai/codex@$CODEX_VERSION" \
  && ! command -v bwrap
LABEL com.symphony.codex.version=$CODEX_VERSION

# Keep revision-only changes out of the dependency and compilation cache keys.
ARG SYMPHONY_VERSION=development
ARG SYMPHONY_REVISION=unknown
ARG SYMPHONY_INPUT_IDENTITY=unknown
ARG TARGETARCH
LABEL org.opencontainers.image.version=$SYMPHONY_VERSION \
      org.opencontainers.image.revision=$SYMPHONY_REVISION \
      com.symphony.architecture=$TARGETARCH \
      com.symphony.inputs.sha256=$SYMPHONY_INPUT_IDENTITY
