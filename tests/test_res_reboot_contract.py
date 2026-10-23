#!/usr/bin/env python3
"""Inspect original RES cloud-init continuation and reboot stages without executing shell."""
from pathlib import Path
import re
import sys
import tarfile

def main(directory):
    directory=Path(directory)
    archive=directory/"installation-scripts.tar.gz"
    with tarfile.open(archive,"r:gz") as tf:
        def member(path):
            return tf.extractfile("./"+path).read().decode()
        install=member("scripts/virtual-desktop-host/linux/install.sh")
        post=member("scripts/virtual-desktop-host/linux/install_post_reboot.sh")
    checks={
        "first lock":'INSTALL_FINISHED_LOCK="${SEMAPHORE_DIR}/install_finished.lock"' in install,
        "first reboot":'reboot' in install and 'PREBAKING_AMI' in install,
        "first handoff":'install_post_reboot.sh' in install,
        "second lock":'INSTALL_POST_REBOOT_FINISHED_LOCK="${SEMAPHORE_DIR}/install_post_reboot_finished.lock"' in post,
        "second reboot":'reboot' in post and 'PREBAKING_AMI' in post,
        "app handoff":'install_app.sh' in post,
    }
    # The original SDK user-data template (not the infrastructure-host
    # template) is verified separately from upstream source in the audit.
    # No real cloud-init boot/reboot occurs in this test.
    for name,ok in checks.items():
        print(("PASS" if ok else "FAIL"),name)
    assert all(checks.values())
if __name__=="__main__":
    main(sys.argv[1])
