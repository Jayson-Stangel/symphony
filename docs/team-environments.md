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

## npm cache in packaged Codex tasks

The maintained Codex reference image supplies a global npm default:
`cache=${PWD}/.npm-cache`. npm expands the current shell directory when it loads
configuration. Setup hooks, ordinary `npm install` / `npm ci`, login shells and
nested checkouts can therefore restore cold dependencies inside their writable
project without changing protected native `HOME` or `CODEX_HOME`. npm lifecycle
children inherit npm's effective cache, which can remain the parent's project
cache after changing directories. No launch export or per-command cache flag is
required. Teams still own dependency setup and registry/network configuration.

This is npm's global configuration, below user and project `.npmrc`, environment
and command-line choices in [npm's configuration precedence](https://docs.npmjs.com/cli/v11/using-npm/config/).
Both `npm_config_cache` and `NPM_CONFIG_CACHE` retain npm's native semantics,
including lowercase lifecycle variables. A team image may replace the global
setting deliberately. A custom `NPM_CONFIG_GLOBALCONFIG`, npm prefix/toolchain,
or shell profile that removes or replaces this configuration must supply its own
writable cache. Host-native sessions and the Claude image keep their existing
npm configuration. Rebuild and select the team image to consume a new reference;
a manager-only update cannot retrofit this default into an old image.

From the actual task shell, `npm config get cache` reports the effective path.
`npm cache verify` exercises that destination before expensive dependency setup.
An explicit inaccessible cache fails with npm's original path and error (for
example `EROFS` or `EACCES`); Symphony does not silently redirect it. Choose a
project-owned path in the team's configuration. Do not make native home/auth
writable, mount a host cache, or grant extra sandbox roots. A symlink outside the
allowed project does not grant write access through the protected sandbox.

The cache is disposable and retained only with its owning workspace. npm creates
it lazily and recreates a pruned cache on the next install; removal of the whole
workspace removes its cache. Do not delete unrelated project data. Add
`.npm-cache/` to the project's ignore rules if needed. Global package installation
still needs a team-owned image/toolchain; this default grants no global writes.
An install-only doctor pass is not project readiness: verify cold dependency
restoration, build and tests through actual protected task commands. Earlier
source308/image88e proof used an explicit cache workaround; it does not qualify
this default. Final assembled-image and real issue-to-PR qualification remain
separate evidence.

## Browser state in packaged Codex tasks

The maintained packaged Codex launch guard supplies `CHROME_CONFIG_HOME` as
`<task workspace>/.symphony-browser-config` when neither `CHROME_CONFIG_HOME`
nor `XDG_CONFIG_HOME` was explicitly supplied. It initializes that owned directory
before starting the native app-server, after configuration and executable-chain
attestation. Ordinary command children and login/non-login shells inherit the
same location; project browser commands do not need an environment prefix or a
patched test script. A trusted project launch wrapper can set an explicit browser
location before the guard runs. Teams still own browser dependencies and project
launch options. A manager-only update cannot change the embedded runtime in an
already selected image; rebuild and select a new team environment to consume it.

Explicit `CHROME_CONFIG_HOME` takes precedence over explicit `XDG_CONFIG_HOME`.
The guard preserves the selected variable and never silently redirects a refused
value. Explicit destinations must be normalized absolute paths below the task
workspace or an already permitted writable root, outside native `HOME`,
`CODEX_HOME`, `.codex` and `.git`. Allowed temporary roots follow the existing task
policy; this feature adds no write roots or network access. Existing directory
components must be owned, writable, ordinary directories without symlinks or
shared write permissions. The permitted shared `/tmp` boundary itself is exempt
from the private-directory mode requirement; its selected descendants are not.
An empty, relative, escaped, linked or foreign destination refuses with
`sandbox_browser_config_unsafe`; read-only or inaccessible state refuses with
`sandbox_browser_config_unwritable`. These codes identify browser state admission,
separately from native authentication or executable-chain refusal. Choose a safe
owned path in the team's configuration instead of making native home writable.

The default is one disposable directory per task workspace, retained for that
workspace's lifetime. Restart reuses it and a pruned directory is recreated.
Existing workspace removal owns its cleanup. There is no automatic age or size
pruning and no recursive browser-specific cleanup of other project data. Include
`.symphony-browser-config/` in team/project ignore rules when appropriate;
Symphony does not change repository ignore rules or native Git configuration.
Readiness validates the real selected destination without creating state there,
then uses disposable diagnostic state. Inherited explicit configuration receives
an owned diagnostic placeholder during wrapper setup; the final guard reverse
maps that location (including a derived suffix) for read-only source validation.
Known validated browser state is excluded from its project copy. Other external
links are omitted from managed diagnostic setup and accepted only when the final
wrapper-selected browser state strictly contains them; root/ancestor links and
unrelated escapes still refuse before native launch. Retained Chromium socket
symlinks are never followed or copied. Unguarded diagnostics retain immediate
external-link refusal. Native HOME/auth stay
outside task write permissions. Host-native Codex sessions and Claude's normal
browser environment retain their existing behavior.

`runner/codex-sandbox-smoke.sh <immutable image>` includes a credential-free
browser fixture using the shipped guarded launch with an empty read-only native
configuration mount and ordinary native `command/exec`. It runs the bundled
headless shell and full Chromium, a second minimal project script, cold/restart/
pruned state, login/non-login shells and native HOME/auth write denial. The outer
fixture has no network, host home, account or existing project mount. This is
supporting protected command-path proof, not the marketing repository acceptance
suite. The plain Docker browser build smoke alone does not establish protected
task readiness. Final acceptance still requires the unchanged marketing browser
commands and assertions on the selected final candidate, native Linux ARM64
components on macOS ARM64 and native Linux AMD64 components on Linux x86-64,
with exact manager/source/provider/image/policy identities. Historical Chrome 155
success after a manual environment override does not qualify this default.

## Evidence boundaries

Supported host policy remains macOS ARM64 and Linux x86-64, with Linux ARM64 and
AMD64 containers for Codex and Claude. Linux ARM64 hosts and Intel macOS are not
supported. New-selection fixture tests, native installed lifecycle tests and real
authenticated repository tasks are separate evidence. A single successful
macOS ARM64/Linux ARM64/Codex task does not qualify Claude or either provider on
Linux x86-64. The delivery evidence ledger records which combinations actually
ran, exact manager and image identities, commands and unresolved gaps.
