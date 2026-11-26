# OpenCode

Install the official Linux x64 OpenCode release (historical baseline 2.3.2) into a versioned directory under /studio/apps/opencode. The current link must resolve to the directory containing the executable opencode. Ensure ripgrep is available (native workstation setup installs it). Install OPENCODE and the no-clobber shared opencode.json using the [shell setup](../shared-applications.md#studio-shell-and-application-commands).

### OpenCode settings and local state

On a fresh local configuration, OPENCODE links only `~/.config/opencode/opencode.json` to `/studio/apps/opencode/config/opencode.json`, honoring `XDG_CONFIG_HOME`. Existing `opencode.json` or `opencode.jsonc` files and links are preserved. The supplied shared setting disables OpenCode's automatic update checks so the shared version is selected deliberately.

Keep OpenCode's database, credentials, caches and service state on local storage; keep XDG directories local, as with ComfyUI. Provider login and local history remain workstation-specific. After changing the shared executable, restart any existing OpenCode background server before checking the running version. The workstation's Ollama service starts independently of `studio env`; entering the shell does not start another server. Existing model files stay in place, and the wrappers do not download models automatically.

The model directory is chosen by the Ollama server when it starts. Running `OLLAMA list` or `OLLAMA run` does not change the model directory of an already-running server; verify its configuration separately.

## Local model workflow

Follow [Ollama](ollama.md) for the startup service, shared model store, custom Modelfile builds and native discovery. Run OLLAMA list, then OPENCODE models or select the Ollama model in /models. The server is localhost:11434, not an EFS scanner. OLLAMA adds the native OpenCode directory after Rez rebuilds PATH, allowing Ollama's Launch OpenCode action. Initialize the shared configuration once with OPENCODE --version before using that action on a fresh profile.

Check a real tool-using coding task, not just catalog discovery. Keep provider credentials and history local. Close existing OpenCode processes before switching current; roll back to the previous executable if needed.


[Installation index](README.md) · [Shared menu and shell setup](../shared-applications.md)
