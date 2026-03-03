"""config.py – Application configuration, load/save."""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent   # project root (one level above src/)
CONFIG_DIR = APP_DIR
CONFIG_FILE = APP_DIR / "config.json"


@dataclass
class Config:
    # ── Playback ──────────────────────────────────────────────────────────────
    retry_delay: float = 3.0
    max_retry_delay: float = 60.0
    max_retries: int = 0           # 0 = unlimited retries

    # ── Grid ──────────────────────────────────────────────────────────────────
    grid_rows: int = 2
    grid_cols: int = 2
    dynamic_grid: bool = False
    active_border: int = 4

    # ── VLC playback ─────────────────────────────────────────────────────────
    vlc_network_cache: int = 3000   # ms – buffer for network streams
    vlc_live_cache: int = 2000      # ms – buffer for live streams

    # ── Stream buffer management ─────────────────────────────────────────────
    smart_buffer: bool = False      # optional stall diagnostics (no forced reconnect)

    # ── Audio ─────────────────────────────────────────────────────────────────
    audio_enabled: bool = True
    single_mode_disconnect_others: bool = True

    # ── DRM / Encryption ─────────────────────────────────────────────────────
    # CENC static key (hex).  M3UPT key: a2226def4bc8f249de2daf36b7c12b1e
    cenc_decryption_key: str = ""

    # ── Upscaling ────────────────────────────────────────────────────────────
    # "off", "lanczos", "sharpen_light", "sharpen_medium", "sharpen_strong"
    upscale_preset: str = "off"

    # ── Playlist / session ────────────────────────────────────────────────────
    default_playlist: str = "https://m3upt.com/iptv"
    remember_session: bool = True

    # ── Favourites ────────────────────────────────────────────────────────────
    favourites: list = field(default_factory=list)       # [{url, name}]
    saved_playlists: list = field(default_factory=list)  # [{name, url}]
    grid_presets: list = field(default_factory=list)     # [{name, rows, cols, dynamic, channels}]

    # ── Runtime state (persisted between runs) ────────────────────────────────
    last_channels: list = field(default_factory=list)  # [{"url":…, "name":…}]
    last_active_index: int = 0
    last_grid_mode: bool = False
    window_x: int = -1          # -1 = let the OS decide
    window_y: int = -1
    window_w: int = 960
    window_h: int = 540


def load_config() -> Config:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            known = Config.__dataclass_fields__
            cfg = Config(**{k: v for k, v in data.items() if k in known})
            return cfg
        except Exception:
            pass
    return Config()


def save_config(cfg: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
