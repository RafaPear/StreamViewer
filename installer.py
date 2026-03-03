#!/usr/bin/env python3
"""StreamsClient GUI Installer — checks requirements, installs deps, creates launcher."""

import os
import platform
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print("ERROR: tkinter is required. Install it:")
    if platform.system() == "Linux":
        print("  sudo apt install python3-tk")
    else:
        print("  Reinstall Python with tcl/tk support.")
    sys.exit(1)

# ── Constants ────────────────────────────────────────────────────────────────

APP_NAME = "StreamsClient"
PROJECT_DIR = Path(__file__).resolve().parent
SRC_DIR = PROJECT_DIR / "src"
VENV_DIR = PROJECT_DIR / ".venv"
REQUIREMENTS = PROJECT_DIR / "requirements.txt"

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

MIN_PYTHON = (3, 10)

VLC_PATHS_MAC = [Path("/Applications/VLC.app")]
VLC_PATHS_WIN = [
    Path("C:/Program Files/VideoLAN/VLC/vlc.exe"),
    Path("C:/Program Files (x86)/VideoLAN/VLC/vlc.exe"),
]

ACCENT = "#0a84ff"
BG = "#1e1e1e"
FG = "#e0e0e0"
FG_DIM = "#888888"
SUCCESS = "#34c759"
ERROR = "#ff453a"
WARNING = "#ffd60a"


# ── Installer Logic ─────────────────────────────────────────────────────────

class Installer:
    """Runs installation steps in a background thread, reports via callbacks."""

    def __init__(self, on_step, on_log, on_done):
        self._on_step = on_step
        self._on_log = on_log
        self._on_done = on_done

    def run(self):
        threading.Thread(target=self._install, daemon=True).start()

    def _log(self, msg, tag="info"):
        self._on_log(msg, tag)

    def _step(self, idx, status):
        self._on_step(idx, status)

    def _install(self):
        ok = True
        try:
            ok = ok and self._check_python()
            ok = ok and self._check_vlc()
            if ok:
                ok = ok and self._setup_venv()
                ok = ok and self._install_deps()
                ok = ok and self._verify()
                if ok:
                    self._create_launcher()
        except Exception as e:
            self._log(f"Unexpected error: {e}", "error")
            ok = False
        self._on_done(ok)

    # ── Steps ────────────────────────────────────────────────────────────

    def _check_python(self):
        self._step(0, "running")
        v = sys.version_info
        self._log(f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
        if (v.major, v.minor) >= MIN_PYTHON:
            self._log(f"Python {v.major}.{v.minor} ✓", "success")
            self._step(0, "done")
            return True
        else:
            self._log(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, "
                       f"found {v.major}.{v.minor}", "error")
            self._step(0, "error")
            return False

    def _check_vlc(self):
        self._step(1, "running")
        found = False
        if IS_MAC:
            for p in VLC_PATHS_MAC:
                if p.exists():
                    self._log(f"VLC found at {p}", "success")
                    found = True
                    break
        elif IS_WIN:
            for p in VLC_PATHS_WIN:
                if p.exists():
                    self._log(f"VLC found at {p}", "success")
                    found = True
                    break
        else:
            if shutil.which("vlc"):
                self._log("VLC found in PATH", "success")
                found = True

        if not found:
            self._log("VLC not found!", "error")
            if IS_MAC:
                self._log("Install: brew install --cask vlc", "info")
                self._log("Or download from videolan.org/vlc", "info")
            elif IS_WIN:
                self._log("Download from videolan.org/vlc", "info")
            else:
                self._log("Install: sudo apt install vlc", "info")
            self._step(1, "error")
            return False

        self._step(1, "done")
        return True

    def _setup_venv(self):
        self._step(2, "running")
        if VENV_DIR.exists():
            self._log("Virtual environment already exists")
            self._step(2, "done")
            return True
        self._log("Creating virtual environment...")
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(VENV_DIR)],
                capture_output=True, text=True, check=True,
            )
            self._log("Virtual environment created ✓", "success")
            self._step(2, "done")
            return True
        except subprocess.CalledProcessError as e:
            self._log(f"Failed to create venv: {e.stderr.strip()}", "error")
            self._step(2, "error")
            return False

    def _install_deps(self):
        self._step(3, "running")
        pip = self._pip_path()
        if not pip:
            self._log("Cannot find pip in venv", "error")
            self._step(3, "error")
            return False

        self._log("Upgrading pip...")
        subprocess.run([str(pip), "install", "--upgrade", "pip", "-q"],
                       capture_output=True)

        self._log("Installing dependencies (this may take a minute)...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(REQUIREMENTS)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            for line in result.stderr.strip().splitlines()[-5:]:
                self._log(line, "error")
            self._step(3, "error")
            return False

        self._log("All dependencies installed ✓", "success")
        self._step(3, "done")
        return True

    def _verify(self):
        self._step(4, "running")
        python = self._python_path()
        result = subprocess.run(
            [str(python), "-c",
             "import sys; sys.path.insert(0,'src');"
             "import vlc; from PyQt6.QtWidgets import QApplication; import qasync;"
             "print('OK')"],
            capture_output=True, text=True, cwd=str(PROJECT_DIR),
        )
        if result.returncode == 0 and "OK" in result.stdout:
            self._log("All modules verified ✓", "success")
            self._step(4, "done")
            return True
        else:
            err = result.stderr.strip().splitlines()
            for line in err[-3:]:
                self._log(line, "error")
            self._step(4, "error")
            return False

    def _create_launcher(self):
        self._step(5, "running")
        if IS_MAC:
            launcher = PROJECT_DIR / "StreamsClient.command"
            launcher.write_text(
                '#!/usr/bin/env bash\n'
                'cd "$(dirname "$0")"\n'
                'source .venv/bin/activate\n'
                'exec python src/streams_client.py "$@"\n'
            )
            launcher.chmod(0o755)
            self._log(f"Created {launcher.name} (double-click to launch)", "success")
        elif IS_WIN:
            launcher = PROJECT_DIR / "StreamsClient.bat"
            launcher.write_text(
                '@echo off\r\n'
                'cd /d "%~dp0"\r\n'
                'call .venv\\Scripts\\activate.bat\r\n'
                'python src\\streams_client.py %*\r\n'
            )
            self._log(f"Created {launcher.name} (double-click to launch)", "success")
        else:
            launcher = PROJECT_DIR / "StreamsClient.sh"
            launcher.write_text(
                '#!/usr/bin/env bash\n'
                'cd "$(dirname "$0")"\n'
                'source .venv/bin/activate\n'
                'exec python src/streams_client.py "$@"\n'
            )
            launcher.chmod(0o755)
            self._log(f"Created {launcher.name}", "success")
        self._step(5, "done")

    # ── Helpers ──────────────────────────────────────────────────────────

    def _pip_path(self):
        if IS_WIN:
            p = VENV_DIR / "Scripts" / "pip.exe"
        else:
            p = VENV_DIR / "bin" / "pip"
        return p if p.exists() else None

    def _python_path(self):
        if IS_WIN:
            return VENV_DIR / "Scripts" / "python.exe"
        return VENV_DIR / "bin" / "python"


# ── GUI ──────────────────────────────────────────────────────────────────────

STEPS = [
    "Check Python",
    "Check VLC",
    "Create environment",
    "Install dependencies",
    "Verify installation",
    "Create launcher",
]

STATUS_ICONS = {
    "pending": "○",
    "running": "◉",
    "done":    "✓",
    "error":   "✗",
}
STATUS_COLORS = {
    "pending": FG_DIM,
    "running": ACCENT,
    "done":    SUCCESS,
    "error":   ERROR,
}


class InstallerWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} Installer")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        # Window size and centering
        w, h = 520, 540
        sx = self.root.winfo_screenwidth()
        sy = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sx-w)//2}+{(sy-h)//2}")

        self._build_ui()
        self._installer = Installer(
            on_step=self._update_step,
            on_log=self._append_log,
            on_done=self._on_done,
        )

    def _build_ui(self):
        root = self.root

        # ── Header ───────────────────────────────────────────────────────
        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=30, pady=(25, 5))

        tk.Label(
            header, text=f"📺  {APP_NAME}", font=("SF Pro Display", 22, "bold"),
            bg=BG, fg=FG,
        ).pack(anchor="w")

        tk.Label(
            header, text="Installer", font=("SF Pro Display", 14),
            bg=BG, fg=FG_DIM,
        ).pack(anchor="w")

        # ── Divider ──────────────────────────────────────────────────────
        tk.Frame(root, bg="#333", height=1).pack(fill="x", padx=30, pady=(15, 15))

        # ── Steps ────────────────────────────────────────────────────────
        steps_frame = tk.Frame(root, bg=BG)
        steps_frame.pack(fill="x", padx=30)

        self._step_labels = []
        self._step_icons = []
        for i, name in enumerate(STEPS):
            row = tk.Frame(steps_frame, bg=BG)
            row.pack(fill="x", pady=2)

            icon = tk.Label(
                row, text=STATUS_ICONS["pending"], font=("SF Mono", 14),
                bg=BG, fg=STATUS_COLORS["pending"], width=2,
            )
            icon.pack(side="left")

            label = tk.Label(
                row, text=name, font=("SF Pro Text", 13),
                bg=BG, fg=FG_DIM, anchor="w",
            )
            label.pack(side="left", padx=(4, 0))

            self._step_icons.append(icon)
            self._step_labels.append(label)

        # ── Log area ─────────────────────────────────────────────────────
        tk.Frame(root, bg="#333", height=1).pack(fill="x", padx=30, pady=(15, 10))

        log_frame = tk.Frame(root, bg="#141414", highlightbackground="#333",
                             highlightthickness=1)
        log_frame.pack(fill="both", expand=True, padx=30)

        self._log_text = tk.Text(
            log_frame, bg="#141414", fg=FG_DIM, font=("SF Mono", 11),
            wrap="word", borderwidth=0, highlightthickness=0,
            state="disabled", height=8,
        )
        self._log_text.pack(fill="both", expand=True, padx=8, pady=8)

        self._log_text.tag_configure("info", foreground=FG_DIM)
        self._log_text.tag_configure("success", foreground=SUCCESS)
        self._log_text.tag_configure("error", foreground=ERROR)
        self._log_text.tag_configure("warning", foreground=WARNING)

        # ── Buttons ──────────────────────────────────────────────────────
        btn_frame = tk.Frame(root, bg=BG)
        btn_frame.pack(fill="x", padx=30, pady=(15, 20))

        self._btn_launch = tk.Button(
            btn_frame, text="Launch App", font=("SF Pro Text", 13),
            bg=ACCENT, fg="white", activebackground="#0070e0",
            activeforeground="white", borderwidth=0, padx=16, pady=6,
            command=self._launch_app, state="disabled",
            disabledforeground="#666",
        )
        self._btn_launch.pack(side="right", padx=(8, 0))

        self._btn_install = tk.Button(
            btn_frame, text="Install", font=("SF Pro Text", 13, "bold"),
            bg=ACCENT, fg="white", activebackground="#0070e0",
            activeforeground="white", borderwidth=0, padx=20, pady=6,
            command=self._start_install,
        )
        self._btn_install.pack(side="right")

        self._btn_quit = tk.Button(
            btn_frame, text="Quit", font=("SF Pro Text", 13),
            bg="#333", fg=FG, activebackground="#444",
            activeforeground=FG, borderwidth=0, padx=16, pady=6,
            command=self.root.quit,
        )
        self._btn_quit.pack(side="left")

    # ── Actions ──────────────────────────────────────────────────────────

    def _start_install(self):
        self._btn_install.configure(state="disabled", text="Installing…")
        self._installer.run()

    def _launch_app(self):
        python = VENV_DIR / ("Scripts/python.exe" if IS_WIN else "bin/python")
        script = SRC_DIR / "streams_client.py"
        if IS_WIN:
            subprocess.Popen([str(python), str(script)],
                             creationflags=subprocess.DETACHED_PROCESS)
        else:
            subprocess.Popen([str(python), str(script)],
                             start_new_session=True)
        self.root.after(500, self.root.quit)

    # ── Callbacks (called from background thread) ────────────────────────

    def _update_step(self, idx, status):
        def _do():
            self._step_icons[idx].configure(
                text=STATUS_ICONS[status],
                fg=STATUS_COLORS[status],
            )
            self._step_labels[idx].configure(
                fg=FG if status in ("running", "done") else STATUS_COLORS[status],
            )
        self.root.after(0, _do)

    def _append_log(self, msg, tag="info"):
        def _do():
            self._log_text.configure(state="normal")
            self._log_text.insert("end", msg + "\n", tag)
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        self.root.after(0, _do)

    def _on_done(self, success):
        def _do():
            if success:
                self._btn_install.configure(text="✓ Installed", bg=SUCCESS)
                self._btn_launch.configure(state="normal")
                self._append_log("\nInstallation complete! Click 'Launch App' to start.", "success")
            else:
                self._btn_install.configure(text="Failed", bg=ERROR)
                self._append_log("\nInstallation failed. Fix the errors above and try again.", "error")
        self.root.after(0, _do)

    # ── Run ──────────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    InstallerWindow().run()
