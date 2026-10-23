# Use the matching architecture's compatible Claude base by immutable digest.
ARG SYMPHONY_BASE
FROM ${SYMPHONY_BASE}
USER 0:0
# Install your team's actual immutable dependencies here. Keep checkout setup in
# WORKFLOW.md hooks; preserve the base's runtime, provider and source labels.
RUN apt-get update && apt-get install -y --no-install-recommends make \
  && rm -rf /var/lib/apt/lists/*
USER 11001:11001
# Preserve HOME, CLAUDE_CONFIG_DIR, working directory, tini and entrypoint.
