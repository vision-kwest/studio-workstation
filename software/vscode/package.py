name = "vscode"
version = "1.146.0"
description = "Microsoft Visual Studio Code desktop editor for Linux x86_64"
requires = ["platform-linux", "arch-x86_64"]
tools = ["code"]
build_command = "python3 {root}/build.py"


def commands():
    env.PATH.prepend("{root}/app/bin")
