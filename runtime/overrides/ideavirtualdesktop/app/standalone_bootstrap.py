# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Standalone entry into the ORIGINAL RES Linux bootstrap; no ResApp lifecycle.

Invoked by the original RES Supervisor installation mechanism. Not a reboot
manager: the original cloud-init user-data and shell semaphores own reentry.
"""
import os
import sys

def main():
    if os.environ.get("STUDIO_STANDALONE") != "true":
        raise RuntimeError("Standalone entrypoint requires STUDIO_STANDALONE=true")
    required = ("IDEA_SESSION_OWNER", "IDEA_SESSION_ID", "RES_BASE_OS",
                "STUDIO_GPU_FAMILIES", "STUDIO_DCV_IDLE_TIMEOUT",
                "STUDIO_DCV_IDLE_WARNING")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing required standalone inputs: " + ", ".join(missing))
    if os.environ["RES_BASE_OS"] != "ubuntu2404":
        raise RuntimeError("Standalone entrypoint supports Ubuntu 24.04 only")
    from ideavirtualdesktop.app.linux import bootstrap
    bootstrap.run()

if __name__ == "__main__":
    main()
