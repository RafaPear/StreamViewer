"""models.py – Channel dataclass and M3U playlist parser."""

import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Channel:
    url: str
    name: str = ""

    def display_name(self) -> str:
        return self.name if self.name else self.url

    def to_dict(self) -> dict:
        return {"url": self.url, "name": self.name}

    @classmethod
    def from_dict(cls, d: dict) -> "Channel":
        return cls(url=d["url"], name=d.get("name", ""))


def parse_m3u(source: str) -> list[Channel]:
    """Parse a standard or extended M3U from a local file path or HTTP(S) URL."""
    if source.startswith("http://") or source.startswith("https://"):
        req = urllib.request.Request(
            source,
            headers={"User-Agent": "Mozilla/5.0 (compatible; StreamsClient/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    else:
        text = Path(source).read_text(encoding="utf-8", errors="replace")

    channels: list[Channel] = []
    pending_name = ""

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line == "#EXTM3U":
            continue
        if line.startswith("#EXTINF"):
            m = re.search(r'tvg-name="([^"]+)"', line)
            pending_name = m.group(1).strip() if m else line.split(",", 1)[-1].strip()
        elif not line.startswith("#"):
            channels.append(Channel(url=line, name=pending_name))
            pending_name = ""

    return channels


@dataclass
class StreamVariant:
    """A single quality variant from an HLS master playlist."""

    url: str
    bandwidth: int = 0
    resolution: str = ""

    @property
    def label(self) -> str:
        parts: list[str] = []
        if self.resolution:
            try:
                h = self.resolution.split("x")[1]
                parts.append(f"{h}p")
            except (IndexError, ValueError):
                parts.append(self.resolution)
        if self.bandwidth:
            mbps = self.bandwidth / 1_000_000
            if mbps >= 1:
                parts.append(f"{mbps:.1f} Mbps")
            else:
                parts.append(f"{self.bandwidth // 1000} kbps")
        return " — ".join(parts) if parts else self.url


def parse_master_playlist(master_url: str) -> list[StreamVariant]:
    """Fetch an HLS master playlist and return available quality variants."""
    if not master_url.startswith(("http://", "https://")):
        return []
    # Quick HEAD check — skip raw TS / binary streams that will never be HLS.
    try:
        head = urllib.request.Request(
            master_url, method="HEAD",
            headers={"User-Agent": "Mozilla/5.0 (compatible; StreamsClient/1.0)"},
        )
        with urllib.request.urlopen(head, timeout=3) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if "video/" in ctype or "octet-stream" in ctype:
                return []
    except Exception:
        return []
    req = urllib.request.Request(
        master_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; StreamsClient/1.0)"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        text = resp.read(64 * 1024).decode("utf-8", errors="replace")
    if "#EXT-X-STREAM-INF" not in text:
        return []
    base = master_url.rsplit("/", 1)[0] + "/"
    variants: list[StreamVariant] = []
    bw, res = 0, ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            attrs = line[len("#EXT-X-STREAM-INF:"):]
            m = re.search(r"BANDWIDTH=(\d+)", attrs)
            bw = int(m.group(1)) if m else 0
            m = re.search(r"RESOLUTION=(\d+x\d+)", attrs)
            res = m.group(1) if m else ""
        elif not line.startswith("#") and line and (bw or res):
            url = line if line.startswith("http") else base + line
            variants.append(StreamVariant(url=url, bandwidth=bw, resolution=res))
            bw, res = 0, ""
    variants.sort(key=lambda v: v.bandwidth, reverse=True)
    return variants
