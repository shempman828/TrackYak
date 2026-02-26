import os
import re

imports = set()
ignore_dirs = {".venv", "__pycache__"}

for root, dirs, files in os.walk("."):
    # Remove ignored dirs from traversal
    dirs[:] = [d for d in dirs if d not in ignore_dirs]

    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except UnicodeDecodeError:
                with open(path, encoding="latin-1") as f:
                    content = f.read()
            for line in content.splitlines():
                m = re.match(r"^\s*(?:import|from)\s+([a-zA-Z0-9_\.]+)", line)
                if m:
                    imports.add(m.group(1).split(".")[0])

print("\n".join(sorted(imports)))
