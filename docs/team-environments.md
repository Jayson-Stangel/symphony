# Team-owned environments

Teams own final repository dependencies. Installed Symphony can durably select a
compatible locally available environment for each project. It pins the actual
immutable image configuration ID, so moving a tag cannot change that selection.
The host manager and the embedded runtime retain their own source identities.

From a project checkout with the installed command:

```bash
symphony environment select --agent codex team/project:reviewed
symphony environment show
symphony environment show --json
symphony auth codex check
symphony runner doctor
symphony run --agent codex
```

Obtain or build the image first using your team's normal tools. Selection never
pulls, builds, exports or uploads an image and never falls back to a reference
image. Missing or incompatible images refuse before changing the selection.
Selection runs a credential-free, network-disabled executable probe once; later
starts and manager updates check immutable identity and compatibility without
repeating image qualification. A selected provider mismatch refuses explicitly.
An existing runner keeps running until an updater-controlled transition:

```bash
symphony update --agent codex
```

A manager-only update retains the selected image, including after stop/restart.
To change environments, explicitly select another compatible image and transition
with the same installed updater. Recovery and rollback remain updater operations;
do not manually replace containers or edit installation/selection files. Selection
shares installation and project locks with updates and refuses a pending recovery.
Selections are private owned JSON records under the configured runner storage,
keyed by the existing canonical repository identity. Another project's selection
is independent. `environment show` reports the desired selection; `runner status`
reports the actually running image during a pending explicit environment change.

## Requirements and maintained templates

Start from the matching compatible reference environment pinned by digest, using
[the Codex template](../runner/templates/team-codex.Dockerfile) or
[the Claude template](../runner/templates/team-claude.Dockerfile). These are
extension templates for team builds, not an image-building service. Preserve:

- Linux container architecture: ARM64 for macOS ARM64, AMD64 for Linux x86-64.
- Provider and protocol (`com.symphony.agent`, `com.symphony.protocol=1`), original
  embedded runtime revision (`org.opencontainers.image.revision`) and runtime files.
- Codex root layout and `/usr/local/bin/symphony-runner` entrypoint, executable
  Codex, `/root/.codex` native credential mount, workspace and state directories.
- Claude UID/GID `11001:11001`, `/home/claude` native profile mount, its HOME/config
  environment and working directory, and the original tini/process-group entrypoint.
- `/opt/symphony/elixir/bin/symphony`, operational skills and provider integration.
  Compatibility metadata is checked alongside actual user/entrypoint/platform and
  an initial runtime-file probe; a label alone does not establish compatibility.

Do not embed accounts, tokens or repository credentials in an image. Native auth
continues to use isolated managed volumes. Existing workspace hooks handle
checkout-specific package restoration and generated files with their existing
failure/timeout rules. External services remain team-managed and must be reachable
under the current isolation rules; Symphony does not add service orchestration.

The Codex example installs ESPHome 2025.9.3 and yamllint for the real
`Jayson-Stangel/home-assistant-voice-te` repository. Its build workflow selects
`home-assistant-voice.factory.yaml` and `home-assistant-voice.8mb.yaml`, which add
provisioning to the reusable `home-assistant-voice.yaml` base. The base alone
requires WiFi/provisioning configuration. The dependency-bearing configuration
check uses the actual factory target:

```bash
esphome config home-assistant-voice.factory.yaml
```

The template installs Python's venv OS support before creating its isolated tool
environment. It links the repository tools into `/usr/local/bin` because the
agent's login shell can replace image `ENV PATH`. The build tests that same login
shell path with actual version commands. Use the team's existing setup commands
rather than assuming every project needs these packages.

Before selection, qualify the team's image with a credential-free login-shell
smoke check using the team's image tag:

```bash
docker run --rm --network none --read-only --entrypoint /bin/bash team/project:reviewed \
  -lc 'command -v esphome && esphome version && command -v yamllint && yamllint --version'
```

This verifies tool discovery in the agent's shell environment; the installed
selector and authenticated repository task remain separate acceptance checks.

## Evidence boundaries

Supported host policy remains macOS ARM64 and Linux x86-64, with Linux ARM64 and
AMD64 containers for Codex and Claude. Linux ARM64 hosts and Intel macOS are not
supported. New-selection fixture tests, native installed lifecycle tests and real
authenticated repository tasks are separate evidence. A single successful
macOS ARM64/Linux ARM64/Codex task does not qualify Claude or either provider on
Linux x86-64. The delivery evidence ledger records which combinations actually
ran, exact manager and image identities, commands and unresolved gaps.
