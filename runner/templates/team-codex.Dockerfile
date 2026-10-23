# Pin a compatible Symphony Codex reference by immutable digest. Build using your
# team's normal tools; installed Symphony selects the resulting local image ID.
ARG SYMPHONY_BASE
FROM ${SYMPHONY_BASE}

# Example for the real home-assistant-voice-te repository. OS/toolchain packages
# belong here; checkout-specific setup stays in WORKFLOW.md hooks.
USER 0:0
RUN apt-get update && apt-get install -y --no-install-recommends python3-venv \
  && rm -rf /var/lib/apt/lists/* \
  && python3 -m venv /opt/team/venv \
  && /opt/team/venv/bin/pip install --no-cache-dir esphome==2025.9.3 yamllint==1.37.1
ENV PATH="/opt/team/venv/bin:${PATH}"
# Preserve the inherited entrypoint, provider/protocol, skills and runtime source
# labels. A host manager update does not change the embedded runtime revision.
