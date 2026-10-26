# Select an installed Symphony command

The per-user installer prints the exact installed executable, the command a fresh
PATH lookup selects, and any conflict. Installing files does not change the
calling terminal's PATH or its command cache. Another account's source command,
a stable install, or a shell alias may still take precedence.

After a normal installation, copy the `source` command printed by the installer.
It selects that installation in the current **bash or zsh** terminal. For example,
with default application storage and no runner namespace:

```sh
source "$HOME/.local/share/symphony/activate.sh"
type -a symphony
symphony --version
symphony doctor
```

For a candidate installed in a custom application directory, use the **exact
activation-file path printed by that installer**. Do not substitute the default
path above or another installation's file. You do not need to locate a release's
`current/scripts` directory or replace any command or skill links.

Activation selects the installed bin directory, clears the shell command cache,
unsets a development `SYMPHONY_HOME` override, and restores that installation's
application, bin, configuration, skills and namespace settings. An explicit
`SYMPHONY_RUNNER_HOME` override is retained; otherwise runner storage continues
to follow the selected configuration, including a later `--config-dir` option.
It also restores explicit Docker runtime/context/host/config overrides selected
at install time; otherwise the Docker CLI's current context applies. It does not
copy account credentials, log in, start a listener or change Docker contexts.
The generated file is private to the installing user. Rerunning the installer
for the same release refreshes it with that invocation's settings.

## Fresh terminals and headless use

To select this installation by default, explicitly add the same printed `source`
line to your own `~/.zshrc` for zsh, or `~/.bashrc` for interactive bash. Bash login
shells must source `~/.bashrc` from their existing login profile. The installer
never edits those files. For a headless script, source the activation file before
calling Symphony. This uses the same context as the terminal installation.

If `type -a symphony` reports an alias or function, inspect your own shell
configuration: aliases/functions can override PATH even after activation. The
installer cannot inspect the parent shell's aliases or cached commands. Its
PATH diagnostic is a fresh lookup and never executes a conflicting command.
Do not remove or overwrite another installation's links to resolve a PATH issue.

## Check the effective context

Run the exact installed executable with `doctor` when command selection is in
doubt. Its human-readable output identifies the running launcher, installed
executable, fresh PATH lookup, effective application/configuration/skills/runner
paths, namespace, container runtime and configured Docker context. A Docker host
override is identified without printing its endpoint. Installation readiness and
shell command selection are reported separately; neither proves authentication,
project readiness or a completed task.

An older installation updated to this manager may not have a saved activation
file. In that case doctor prints an equivalent bash/zsh activation block for the
current effective context instead of pointing to a missing file. Retain that
block explicitly if wanted. Custom settings must be supplied when diagnosing an
older installation; doctor cannot infer overrides that were never saved.
