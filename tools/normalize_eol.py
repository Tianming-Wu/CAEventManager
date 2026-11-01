"""Normalise all source files to LF.

The patch scripts used during development wrote through Python's text mode, which
on Windows turns every "\\n" into "\\r\\n".  Harmless for .py/.js/.html, fatal for
.sh (the shebang becomes #!/bin/sh\\r and the kernel cannot find the interpreter),
and noisy for git (a one-line edit becomes a whole-file diff).

Run this before committing and before deploying.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {"data", "tmp", "__pycache__", ".git", ".vscode", "node_modules"}
SUFFIXES = {".py", ".js", ".css", ".html", ".sh", ".ps1", ".md", ".json"}

changed = []
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or path.suffix not in SUFFIXES:
        continue
    rel = path.relative_to(ROOT)
    if any(part in SKIP_DIRS for part in rel.parts):
        continue
    data = path.read_bytes()
    if b"\r\n" in data:
        path.write_bytes(data.replace(b"\r\n", b"\n"))
        changed.append(str(rel))

print("normalised to LF:", len(changed), "file(s)")
for c in changed:
    print("  ", c)

print()
print("still CRLF:", sum(
    1 for p in ROOT.rglob("*")
    if p.is_file() and p.suffix in SUFFIXES
    and not any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts)
    and b"\r\n" in p.read_bytes()))
