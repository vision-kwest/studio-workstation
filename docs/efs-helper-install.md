# Studio EFS mount helper provisioning

CPU/GPU creation includes the approved immutable Rez 3.4.0 bundle and official `AmazonEFSUtils` installation through SSM Distributor by default; `--no-studio-software` opts out. After DCV readiness, the CLI waits for helper installation; the separate software service completes asynchronously. The bundle contains no EFS `.deb`.

The guest `studio-software-setup.service` waits for DCV readiness and then requires `mount.efs`. If the EFS helper is not yet installed, it fails and retries after 30 seconds; it does not change EFS authorization or block DCV. After the helper appears, the existing script installs Rez from the immutable, SHA-256-verified S3 bundle and mounts `fs-054fd0b72429f4569` read-only using `tls,iam`.

A clean software setup requires SSM connectivity, `AWS-ConfigureAWSPackage`, and Distributor package `AmazonEFSUtils`. Helper failures report incomplete software setup while DCV remains available and GPU expiration safeguards remain armed.

Live acceptance (2026-11-11): On GPU `i-04b5259a90dc8bf50`, SSM Distributor installed `amazon-efs-utils 3.3.5-1`. The setup service then succeeded, the read-only EFS mount resolved production `ollama-0.40`, and `qwen2.5:0.5b` produced `SUCCESS` while the Tesla T4 showed the Ollama `llama-server` process consuming 580 MiB VRAM. The permanent automatic provisioning path has CI tests but has not yet been tested on a newly launched workstation.
