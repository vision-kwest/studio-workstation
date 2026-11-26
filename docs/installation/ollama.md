# Ollama

Ollama is delivered through the existing production Rez package, not a second GUI archive. Install the shared OLLAMA wrapper using the common shell setup. The server is per workstation; model files are shared. Do not start a duplicate server.

### Connect OpenCode to a local Ollama model

A running Ollama server needs an installed completion model before OpenCode can offer a local choice. OpenCode 2.3.2 [discovers these models automatically](https://opencode.ai/docs/providers/#ollama) at `http://127.0.0.1:11434`; an empty `OLLAMA list` means there is nothing local to select yet.

The model files are shared, but each workstation runs its own Ollama server. OpenCode discovers models through that local server, not by scanning EFS. Normal CPU/GPU setup installs and enables `studio-ollama.service` after Rez is ready. On later boots, systemd runs `/studio/bin/OLLAMA serve` as the desktop account with `OLLAMA_MODELS=/studio/models/ollama` and a listener at `127.0.0.1:11434`. It retries failures every 30 seconds without making desktop readiness depend on EFS or Ollama.

The service follows Ollama's [native Linux startup-service approach](https://docs.ollama.com/linux#adding-ollama-as-a-startup-service-recommended). It does not pull or preload a model. Model weights load when requested; GPU discovery may still use transient resources. After inference, normal Ollama [keep-alive behavior](https://docs.ollama.com/faq#how-do-i-keep-a-model-loaded-in-memory-or-make-it-unload-immediately) applies. Model files, Modelfile parameters and OpenCode settings are preserved. Set server-wide environment overrides with `sudo systemctl edit studio-ollama.service`; a system service does not inherit exports from your terminal.

On a replacement workstation, confirm the existing shared models and open OpenCode; no second terminal or repeated model download is needed:

```bash
studio env
OLLAMA list
OPENCODE
```

For an existing workstation, apply the updated setup from the repository once. Finish active model requests and stop any manually launched `OLLAMA serve` first, so it releases port 11434:

```bash
sudo install -m 700 scripts/setup_workstation_software.sh /usr/local/sbin/studio-software-setup
sudo systemctl restart --no-block studio-software-setup.service
```

The setup keeps an already managed Ollama server running when repeated. Inspect the service and its logs with:

```bash
systemctl status studio-ollama.service --no-pager
journalctl -u studio-ollama.service -b --no-pager -n 50
curl --fail --silent --show-error http://127.0.0.1:11434/api/ps
```

Before the first model request, `/api/ps` should report an empty `models` array. Use `sudo systemctl restart studio-ollama.service` after changing server environment overrides, or `sudo systemctl stop studio-ollama.service` to stop it temporarily. Setup re-enables it on subsequent boots. `OLLAMA -h` works without a server. `OLLAMA serve` remains available for deliberate foreground debugging after stopping the managed service.

If the shared store has no suitable model yet, install one once with `OLLAMA pull qwen3:8b`. [Qwen3 8B](https://ollama.com/library/qwen3:8b) is an initial tool-capable example with an approximately 5.2 GB download. Select it under Ollama in OpenCode's `/models` picker. Discovery refreshes about every 30 seconds; reopen the picker after the download finishes. `OPENCODE models` lists the catalog from a terminal. Choose model size and context settings for the workstation's memory; discovering a model does not establish that every context length will fit.

The OLLAMA wrapper also exposes `/studio/apps/opencode/current` to Ollama and its children when the native executable exists. It adds the path after Rez resolves the package environment, because Rez rebuilds PATH. This lets Ollama's **Launch OpenCode** menu reuse the shared binary. `OPENCODE` remains the uppercase Studio wrapper; no lowercase wrapper is installed. On a fresh local profile, run `OPENCODE --version` once before launching OpenCode through Ollama's menu so the existing shared-settings link is initialized. Ollama invokes the native executable directly.

Model downloads follow an explicit pull or model selection; `OLLAMA run` can also download a missing model. Models in `/studio/models/ollama` remain available to replacement workstations using the same shared store.

**Verification:** See [Issue #140](https://github.com/vision-kwest/studio-workstation/issues/140) for live shell activation evidence and [Issue #147](https://github.com/vision-kwest/studio-workstation/issues/147) for Ollama/OpenCode integration acceptance. GUI application and model-inference checks are separate from command resolution.

## Model context tuning

The November 23 OpenCode experiment found poor results with Qwen3.5 9B at the original context setting, improvement at 65,536 and a reported successful 131,072-token configuration. The saved Modelfile is /studio/models/ollama/modelfile/qwen3.5_9b_128k_context. Inspect it before rebuilding; the full historical contents were not recovered. The following is a minimal reproducible example, not a byte-for-byte copy:

```text
FROM qwen3.5:9b
PARAMETER num_ctx 131072
```

Save a new Modelfile without overwriting the historical one, then run:

```bash
studio env
OLLAMA create qwen3.5-9b-128k -f /studio/models/ollama/modelfile/qwen3.5_9b_128k_context
OLLAMA show --modelfile qwen3.5-9b-128k
OLLAMA ps
```

The create command uses the existing saved file; use your new file path instead if experimenting. Select the resulting model in OpenCode. A server-wide alternative is OLLAMA_CONTEXT_LENGTH=65536 in a systemd override, followed by a service restart; per-model num_ctx allows distinct variants. Both g4dn sizes use a T4 with 16 GiB VRAM; the larger instance adds host RAM, not VRAM. Long context increases KV-cache memory and may cause CPU offload or slower inference. Compare real tool tasks, latency and OLLAMA ps/nvidia-smi; 128K is an observed useful setting, not a universal optimum or guarantee of full GPU residency.


[Installation index](README.md) · [Shared menu and shell setup](../shared-applications.md)
