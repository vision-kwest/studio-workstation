name = "ollama_acceptance"
version = "0.40.16"
requires = []
build_command = "bash {root}/build.sh"

def commands():
    env.PATH.prepend("{root}/bin")
    env.LD_LIBRARY_PATH.prepend("{root}/lib")
