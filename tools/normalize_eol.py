"""Normalise all source files to LF.

The patch scripts used during development wrote through Python's text mode, which
on Windows turns every "\\n" into "\\r\\n".  Harmless for .py/.js/.html, fatal for
.sh (the shebang becomes #!/bin/sh\\r and the kernel cannot find the interpreter).
"""
import pathlib

ROOTS = ("app.py", "db.py", "model.py", "auth.py", "localconfig.py", "checkin.py",
         "pinyintable.py", "run.sh", "service.sh", "README.md", "deploy.ps1")
DIRS = ("tpl", "static", "tools")

changed = []
for name in ROOTS:
    p = pathlib.Path(name)
    if not p.is_file():
        continue
    data = p.read_bytes()
    if b"\r\n" in data:
        p.write_bytes(data.replace(b"\r\n", b"\n"))
        changed.append(name)

for d in DIRS:
    for p in pathlib.Path(d).rglob("*"):
        if p.is_file() and p.suffix in (".py", ".js", ".css", ".html", ".sh", ".ps1"):
            data = p.read_bytes()
            if b"\r\n" in data:
                p.write_bytes(data.replace(b"\r\n", b"\n"))
                changed.append(str(p))

print("normalised to LF:", len(changed), "file(s)")
for c in changed:
    print("  ", c)
