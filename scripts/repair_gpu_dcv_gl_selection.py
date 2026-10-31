#!/usr/bin/env python3
"""Review-only repair of the registered GPU DCV component (0.1.2 -> 0.1.3).

Select exactly one binary package whose Debian control Package field is
'nice-dcv-gl'. Other gl* packages in the DCV archive are not interchangeable.
No AWS resources are mutated by this script.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
import yaml

OLD = 'gl=(packages/nice-dcv-gl*_amd64.ubuntu2404.deb)'
OLD_CHECK = 'test "${#gl[@]}" -eq 1'
NEW = """# Inspect package metadata instead of trusting an ambiguous gl* filename glob.
# The DCV distribution may also contain nice-dcv-gltest.
gl_candidates=(packages/nice-dcv-gl*_amd64.ubuntu2404.deb)
printf 'DCV GL candidates: %s\\n' "${gl_candidates[@]}"
gl=()
for candidate in "${gl_candidates[@]}"; do
  package_name=$(dpkg-deb -f "$candidate" Package)
  printf 'DCV package %s: %s\\n' "$candidate" "$package_name"
  if [[ "$package_name" == "nice-dcv-gl" ]]; then
    gl+=("$candidate")
  fi
done
if [[ "${#gl[@]}" -ne 1 ]]; then
  printf 'ERROR: expected exactly one nice-dcv-gl binary package, found %s\\n' "${#gl[@]}" >&2
  exit 43
fi"""
def replace_once(s, a, b):
    if s.count(a) != 1:
        raise ValueError(f"Expected one occurrence of {a!r}; got {s.count(a)}")
    return s.replace(a, b)

def generate(source):
    doc = yaml.safe_load(source)
    if doc.get("name") != "studio-gpu-dcv-res-stage":
        raise ValueError("Expected registered GPU DCV component")
    steps = doc["phases"][0]["steps"]
    if [s["name"] for s in steps] != ["InstallDCV", "RebootAfterDCV"]:
        raise ValueError("Unexpected DCV steps")
    commands = steps[0]["inputs"]["commands"]
    if len(commands) != 1:
        raise ValueError("Expected exactly one DCV command block")
    script = commands[0]
    script = replace_once(script, OLD, NEW)
    script = replace_once(script, OLD_CHECK, '# Selected by Debian package metadata above.')
    assert 'apt-get install -y "./${server[0]}" "./${viewer[0]}" "./${xdcv[0]}" "./${gl[0]}"' in script
    assert 'printf "%s  %s\\n" "$expected" dcv.tgz | sha256sum -c -' in script
    commands[0] = script
    doc["description"] = "CPU DCV install plus metadata-selected nice-dcv-gl (reject ambiguous matches)"
    return doc

def validate(doc):
    steps = doc["phases"][0]["steps"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh") as f:
        f.write(steps[0]["inputs"]["commands"][0])
        f.flush()
        subprocess.run(["bash", "-n", f.name], check=True)
    assert steps[1]["action"] == "Reboot"
    print("PASS: GPU DCV shell syntax, metadata selector, CPU install path preserved")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    doc = generate(a.source.read_text())
    validate(doc)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(yaml.safe_dump(doc, sort_keys=False, width=120))
    print("REVIEW ONLY:", a.output)

if __name__ == "__main__":
    main()
