FROM node:20-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    gh \
    git \
    openssh-server \
    python3 \
    ripgrep \
  && rm -rf /var/lib/apt/lists/*

RUN install -d -m 700 /root/.ssh /run/symphony/ssh /run/secrets /var/run/sshd /workspaces

RUN npm install --global @openai/codex@latest

COPY sshd_config /etc/ssh/sshd_config.d/symphony-runner.conf
COPY entrypoint.sh /usr/local/bin/symphony-runner
RUN chmod 755 /usr/local/bin/symphony-runner

EXPOSE 22

ENTRYPOINT ["/usr/local/bin/symphony-runner"]
