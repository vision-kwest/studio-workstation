# Studio publishing recipes

Recipes are reviewed shell scripts executed by the existing Studio publisher with `publisher-run --operation build` at a pinned 40-character Git commit. The publisher's EFS write authority is broad: review recipes before running them. Do not run untrusted code.

## Ollama

`recipes/ollama/build.sh` publishes the official Linux AMD64 Ollama v0.40.16 distribution, verified against the upstream SHA-256 digest, as Rez `ollama/0.40` in `dev`. It refuses to overwrite an existing package. The two-part Rez version is necessary for the current publisher promotion CLI; the full upstream version is in `manifest.json`.

From the authorized Studio CLI checkout, start the existing publisher, check readiness, and submit the build using this branch's **reviewed commit SHA**:

```bash
python3 studio workstation publisher-start --instance-id i-0499bf014d1fc86eb
python3 studio workstation publisher-run --instance-id i-0499bf014d1fc86eb --operation check
python3 studio workstation publisher-run --instance-id i-0499bf014d1fc86eb --operation build --recipe-repo vision-kwest/studio-workstation --recipe-ref FULL_40_CHARACTER_COMMIT --recipe-path recipes/ollama/build.sh
python3 studio workstation publisher-result --instance-id i-0499bf014d1fc86eb --command-id SSM_COMMAND_ID
python3 studio workstation publisher-run --instance-id i-0499bf014d1fc86eb --operation promote-test --name ollama --version 0.40
python3 studio workstation publisher-run --instance-id i-0499bf014d1fc86eb --operation promote-prod --name ollama --version 0.40
python3 studio workstation publisher-stop --instance-id i-0499bf014d1fc86eb
```

On a workstation with Rez configured, use `rez-env ollama-0.40 -- ollama --version` and `rez-env ollama-0.40 -- ollama serve`. Keep models on separate persistent writable storage; EFS software packages must remain read-only for workstation consumers. Ollama defaults to localhost port 11434. GPU inference must be tested separately on the Tesla T4.
