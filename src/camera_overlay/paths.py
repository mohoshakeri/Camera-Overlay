from pathlib import Path


def package_dir() -> Path:
    """Return The Installed Package Directory."""
    return Path(__file__).resolve().parent


def project_root() -> Path:
    """Return The Repository Root Containing Assets And Scripts."""
    return package_dir().parents[1]


def assets_dir() -> Path:
    """Return The Shared Assets Directory."""
    return project_root() / "assets"


def icon_path() -> Path:
    """Return The Application Icon Path."""
    return assets_dir() / "icon.png"


def src_dir() -> Path:
    """Return The Python Sources Root Used On PYTHONPATH."""
    return project_root() / "src"
