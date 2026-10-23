# Keep this Node pin aligned with runner/Dockerfile and the other reference image.
FROM node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git tini \
  && rm -rf /var/lib/apt/lists/* \
  && groupadd --gid 11001 claude \
  && useradd --uid 11001 --gid 11001 --home-dir /home/claude claude \
  && install -d -o claude -g claude -m 700 /home/claude /lab-workspace

WORKDIR /opt/symphony-claude-lab
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund && npm cache clean --force
COPY runtime.mjs ./
COPY qualification ./qualification
RUN chmod -R a-w /opt/symphony-claude-lab

ENV HOME=/home/claude \
    CLAUDE_CONFIG_DIR=/home/claude/.claude \
    CLAUDE_CODE_DISABLE_FAST_MODE=1 \
    DISABLE_AUTOUPDATER=1
USER 11001:11001
WORKDIR /lab-workspace
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "node", "/opt/symphony-claude-lab/qualification/lab.mjs"]
CMD ["inspect"]
