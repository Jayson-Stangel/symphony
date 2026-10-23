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
  && /opt/team/venv/bin/pip install --no-cache-dir esphome==2025.9.3 yamllint==1.37.1 \
  && ln -s /opt/team/venv/bin/esphome /usr/local/bin/esphome \
  && ln -s /opt/team/venv/bin/yamllint /usr/local/bin/yamllint
ENV PATH="/opt/team/venv/bin:${PATH}"
# Codex invokes login shells, whose profile may replace image ENV PATH. Keep the
# actual repository tools reachable through the standard /usr/local/bin path.
RUN /bin/bash -lc 'command -v esphome && esphome version && command -v yamllint && yamllint --version'
# Preserve the inherited entrypoint, provider/protocol, skills and runtime source
# labels. A host manager update does not change the embedded runtime revision.
