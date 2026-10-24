# Release preparation

After cloning this repository and downloading the two original, checksum-pinned RES 2026.06 archives into `~/studio-release/original/`, run:

```bash
python3 scripts/release.py
```

This verifies original SHA-256 hashes, builds each derived archive twice, checks byte-identical reproducibility, runs the existing archive-preservation tests, and writes the verified outputs to `~/studio-release/derived/` with a `~/studio-release/release-manifest.json`.

Use `--release-dir PATH` to choose another release directory.

This command **does not** upload to S3, launch EC2, or deploy CloudFormation. The original downloads are not performed by this command; if they are missing, it fails with a clear error. Publishing is a separate explicitly approved step.
