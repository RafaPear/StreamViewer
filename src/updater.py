"""updater.py – Self-update mechanism using GitHub Releases.

Checks the latest release on GitHub, compares versions, and downloads +
applies updates with a platform-specific strategy:

* **macOS** – replaces the running ``.app`` bundle via a small shell script
  that waits for the process to exit, copies the new bundle, and relaunches.
* **Windows** – writes a ``.bat`` script that waits, replaces files, and
  relaunches.
* **Development mode** (not frozen) – update check works but apply is skipped.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request

from version import __version__

GITHUB_REPO = "RafaPear/StreamViewer"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

logger = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def check_for_update() -> dict | None:
    """Check GitHub for a newer release.

    Returns a dict with *version*, *url*, *name*, *size*, *notes* if an update
    is available, or ``None`` if already up-to-date / check failed.
    """
    try:
        req = Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        logger.warning("Update check failed: %s", exc)
        return None

    tag = data.get("tag_name", "").lstrip("v")
    if not tag or not _is_newer(tag, __version__):
        return None

    suffix = "macOS.zip" if sys.platform == "darwin" else "Windows.zip"
    asset = next((a for a in data.get("assets", []) if a["name"].endswith(suffix)), None)
    if asset is None:
        return None

    return {
        "version": tag,
        "url": asset["browser_download_url"],
        "name": asset["name"],
        "size": asset.get("size", 0),
        "notes": data.get("body", ""),
    }


def download_and_apply(url: str, on_progress=None) -> bool:
    """Download the update archive and prepare for installation.

    *on_progress(downloaded_bytes, total_bytes)* is called periodically.
    Returns ``True`` if the update was staged and the app should quit.
    """
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="streamsclient_update_"))
        zip_path = tmp_dir / "update.zip"

        # Download
        req = Request(url)
        with urlopen(req, timeout=300) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and total:
                        on_progress(downloaded, total)

        # Extract
        extract_dir = tmp_dir / "extracted"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

        # Apply
        if sys.platform == "darwin":
            return _apply_macos(extract_dir)
        elif sys.platform == "win32":
            return _apply_windows(extract_dir)
        else:
            logger.info("Auto-update not supported on this platform")
            return False
    except Exception as exc:
        logger.error("Update failed: %s", exc)
        return False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_newer(remote: str, local: str) -> bool:
    """Return True if *remote* is a higher semver than *local*."""
    try:
        r = tuple(int(x) for x in remote.split("."))
        l = tuple(int(x) for x in local.split("."))
        return r > l
    except (ValueError, AttributeError):
        return False


def _get_app_path() -> Path:
    """Return the path to the running application bundle / directory."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        if sys.platform == "darwin":
            # Walk up from .app/Contents/MacOS/StreamsClient → .app
            p = exe
            while p.suffix != ".app" and p != p.parent:
                p = p.parent
            return p
        return exe.parent  # Windows: directory containing the exe
    return Path(__file__).resolve().parent.parent  # Development: project root


def _apply_macos(extract_dir: Path) -> bool:
    """Stage a macOS .app replacement and launch an updater script."""
    apps = list(extract_dir.rglob("*.app"))
    if not apps:
        logger.error("No .app bundle found in update archive")
        return False
    new_app = apps[0]
    current_app = _get_app_path()

    if not getattr(sys, "frozen", False) or current_app.suffix != ".app":
        logger.info("Not running as .app — skipping apply (dev mode)")
        return False

    script = (
        "#!/bin/bash\n"
        "sleep 1\n"
        f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done\n"
        f'rm -rf "{current_app}"\n'
        f'cp -R "{new_app}" "{current_app}"\n'
        f'open "{current_app}"\n'
        f'rm -rf "{extract_dir.parent}"\n'
    )
    script_path = extract_dir.parent / "update.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)
    subprocess.Popen(["bash", str(script_path)], start_new_session=True)
    return True


def _apply_windows(extract_dir: Path) -> bool:
    """Stage a Windows directory replacement and launch an updater batch file."""
    dirs = [d for d in extract_dir.iterdir() if d.is_dir()]
    new_dir = dirs[0] if dirs else extract_dir
    current_dir = _get_app_path()
    exe_name = Path(sys.executable).name if getattr(sys, "frozen", False) else "StreamsClient.exe"

    if not getattr(sys, "frozen", False):
        logger.info("Not running as frozen exe — skipping apply (dev mode)")
        return False

    script = (
        "@echo off\n"
        "timeout /t 2 /nobreak >nul\n"
        ":wait\n"
        f'tasklist /FI "PID eq {os.getpid()}" | find "{os.getpid()}" >nul\n'
        "if not errorlevel 1 (\n"
        "    timeout /t 1 /nobreak >nul\n"
        "    goto wait\n"
        ")\n"
        f'xcopy /E /Y /Q "{new_dir}\\*" "{current_dir}\\"\n'
        f'start "" "{current_dir}\\{exe_name}"\n'
        f'rmdir /S /Q "{extract_dir.parent}"\n'
    )
    script_path = extract_dir.parent / "update.bat"
    script_path.write_text(script)
    # DETACHED_PROCESS = 0x00000008
    subprocess.Popen(
        ["cmd", "/c", str(script_path)],
        creationflags=0x00000008,
    )
    return True
