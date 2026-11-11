# Studio EFS mount helper provisioning

The immutable Rez 3.4.0 bundle does **not** include an `amazon-efs-utils_*.deb` file. Workstation creation with `--rez-version-id` now provisions the official `AmazonEFSUtils` package through AWS Systems Manager Distributor, using the workstation instance's SSM agent. The Studio CLI waits for Distributor success before proceeding with GPU DCV/ACM setup or reporting CPU create completion.

The guest `studio-software-setup.service` waits for DCV readiness and then requires `mount.efs`. If the EFS helper is not yet installed, it fails and retries after 30 seconds; it does not change EFS authorization or block DCV. After the helper appears, the existing script installs Rez from the immutable, SHA-256-verified S3 bundle and mounts `fs-054fd0b72429f4569` read-only using `tls,iam`.

A clean workstation launch requires SSM connectivity, the `AWS-ConfigureAWSPackage` document, and Distributor package `AmazonEFSUtils`. Failures are fatal to the Studio CLI's software-installation stage; GPU expiration and EBS cleanup safeguards remain independently armed.

Live acceptance (2026-11-11): On GPU `i-04b5259a90dc8bf50`, SSM Distributor installed `amazon-efs-utils 3.3.5-1`. The setup service then succeeded, the read-only EFS mount resolved production `ollama-0.40`, and `qwen2.5:0.5b` produced `SUCCESS` while the Tesla T4 showed the Ollama `llama-server` process consuming 580 MiB VRAM. The permanent automatic provisioning path has CI tests but has not yet been tested on a newly launched workstation.
