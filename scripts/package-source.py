"""Create a shareable allowlisted archive, excluding credentials and private data."""
import json
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
archive = root.parent / "lumina-source.zip"
files = [
    "README.md", "package.json", "package-lock.json", "pyproject.toml",
    "requirements.lock", "requirements-dev.lock", "tsconfig.json", "vite.config.ts",
    "playwright.config.ts", "index.html", "Dockerfile", "compose.yaml",
    ".env.example", ".gitignore", ".dockerignore", ".gitattributes",
]
directories = ["src", "server", "bridge", "public", "docs", "scripts", "tests", "e2e", ".github"]
sources = [root / name for name in files]
for directory in directories:
    sources.extend(path for path in (root / directory).rglob("*") if path.is_file())
sources = [
    path for path in sources
    if not path.is_symlink() and path.resolve().is_relative_to(root)
    and "__pycache__" not in path.parts and path.suffix != ".pyc"
    and path.name != "browser-inspection.png"
]
for path in sources:
    if path.name == ".env" or "data" in path.relative_to(root).parts:
        raise RuntimeError("Private state reached the packaging allowlist")
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
    for path in sorted(sources):
        output.write(path, "lumina/" + path.relative_to(root).as_posix())
with zipfile.ZipFile(archive) as output:
    assert output.testzip() is None
    assert "lumina/.env" not in output.namelist()
print(json.dumps({"archive": str(archive), "files": len(sources), "bytes": archive.stat().st_size, "private_state_excluded": True}))
