# StreamsClient

A desktop app for watching multiple live video streams side by side.

---

## What is this?

StreamsClient lets you watch several video streams at the same time in a
grid layout. It works with IPTV channels, security cameras (RTSP), HLS
streams, and anything else VLC can play.

You can:
- Watch 1 stream full-screen or many streams in a grid
- Pick channels from M3U / M3U8 playlists (the format used by most IPTV providers)
- Save your favourite channels so you can find them quickly
- Save entire grid layouts as presets (which streams + grid size)
- Change the video quality per stream
- Control audio, play/pause, and volume for each stream individually

---

## Requirements

You need **VLC** installed on your computer. It is free:

> **Download VLC:** https://www.videolan.org/vlc/

| Platform | Where to install VLC |
|----------|---------------------|
| macOS | Drag to `/Applications` (the default location) |
| Windows | Run the installer normally |
| Linux | `sudo apt install vlc` or your distro's package manager |

If you are running from source (not the built app), you also need
**Python 3.10 or newer**.

---

## Installation

### Quick install (recommended)

Clone the repository and run the installer — it checks everything for you:

**macOS / Linux:**
```bash
git clone https://github.com/RafaPear/StreamViewer.git
cd StreamViewer
./install.sh
```

**Windows:**
```
git clone https://github.com/RafaPear/StreamViewer.git
cd StreamViewer
install.bat
```

The installer will:
- ✓ Check that Python 3.10+ is installed (tells you how to get it if not)
- ✓ Check that VLC is installed (tells you how to get it if not)
- ✓ Create a virtual environment and install all dependencies
- ✓ Create a clickable launcher (`StreamsClient.command` on macOS, `StreamsClient.bat` on Windows)

### Ready-made app (download)

Pre-built apps are available on the
[Releases page](https://github.com/RafaPear/StreamViewer/releases).
Download the `.zip` for your platform:

- **macOS** — unzip, drag `StreamsClient.app` to Applications.
  If macOS blocks it, right-click > Open.
- **Windows** — unzip, double-click `StreamsClient.exe`.
  If SmartScreen warns, click "More info" > "Run anyway".

> **Note:** VLC must still be installed separately.

### Run from source

<details>
<summary><strong>macOS / Linux</strong></summary>

Open a terminal in the project folder and run:

```bash
python3 -m venv .venv          # create a virtual environment (once)
source .venv/bin/activate      # activate it
pip install -r requirements.txt  # install dependencies (once)
./run.sh                       # launch the app
```
</details>

<details>
<summary><strong>Windows</strong></summary>

Open Command Prompt or PowerShell in the project folder and run:

```
python -m venv .venv               # create a virtual environment (once)
.venv\Scripts\activate             # activate it
pip install -r requirements.txt    # install dependencies (once)
python src/streams_client.py           # launch the app
```
</details>

### Build a standalone app yourself

This bundles Python and all dependencies into a single app that anyone
can run without installing Python.

```bash
# macOS / Linux
./build.sh          # output: dist/StreamsClient.app

# Windows
build.bat           # output: dist\StreamsClient\StreamsClient.exe
```

Run `./build.sh clean` (or `build.bat clean`) to delete build files.

---

## Getting started

When you open the app for the first time you will see a welcome screen
with four buttons. Here is what each one does:

| Button | What it does |
|--------|-------------|
| **Add Stream** | Type or paste a single stream URL to start watching it |
| **Load Playlist** | Open an M3U / M3U8 playlist (file or URL) and pick which channels you want |
| **Favourites** | Browse channels you saved before |
| **Presets** | Load a saved grid layout (restores streams + grid size in one click) |

> These same actions are always available from the **bottom toolbar** and
> the **File** menu at the top of the window.

---

## Using the app

### Watching streams

- **Grid view** shows all your streams in a grid. The selected stream has
  a green border.
- **Single-stream view** shows one stream full-size. The controls and
  cursor hide automatically after 2 seconds — move the mouse to bring them
  back.
- **Click** a stream to select it.
- **Double-click** a stream to switch to single-stream view.
- Press **G** to toggle between grid and single-stream.
- Press **F** to toggle fullscreen.
- Use the **Left / Right** arrow keys to cycle through streams.

### Adding and removing streams

- Press **A** to add a new stream by URL.
- Press **L** to load a playlist and pick channels from it.
- Press **Del** to remove the currently selected stream.
- Use **Ctrl+Up / Ctrl+Down** to change the order of streams.

### Each stream has its own control bar

At the bottom of every stream you will see:
- **Play / Pause** button
- **Mute** button and **volume slider**
- **Quality** button (click to pick a specific resolution, if available)
- The stream name and status

### Grid pages

If you have more streams than fit in the grid, the bottom bar shows
page controls. Use **Page Up / Page Down** or the **< Prev** / **Next >**
buttons to navigate.

---

## Saving your setup

### Favourites

Save individual channels you watch often:
1. Select a stream.
2. Press **Ctrl+D** (or go to *File > Add to Favourites*).
3. To load favourites later, click **Favourites** in the toolbar or go to
   *File > Manage Favourites*.

### Grid Presets

Save your entire layout (which streams + grid size) so you can restore it
with one click:
1. Set up your streams and grid the way you want.
2. Go to *View > Save Grid Preset* and give it a name.
3. To load it later, click **Presets** in the toolbar or go to
   *View > Load Grid Preset*.

### Saved Playlists

Save playlist URLs so you do not have to type them every time:
- Go to *File > Saved Playlists* to add, rename, or delete saved playlists.

---

## Settings

Open *File > Preferences* (or press the **,** key) to change:

| Setting | What it controls |
|---------|-----------------|
| Grid rows / columns | How many streams fit on one page |
| Dynamic grid | Automatically calculates grid size based on stream count |
| Network buffer | How much video to buffer (higher = more stable, but more delay) |
| Default playlist | The playlist URL loaded when you start the app |

---

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| Right / Left | Next / previous stream |
| G | Toggle grid / single-stream view |
| F | Toggle fullscreen |
| A | Add a stream |
| L | Load a playlist |
| Del | Remove selected stream |
| Ctrl+D | Add selected stream to favourites |
| Ctrl+Up / Ctrl+Down | Reorder streams |
| Page Up / Page Down | Navigate grid pages |
| , (comma) | Open preferences |
| Ctrl+Q | Quit |

---

## Command-line options (advanced)

You can launch the app with options from a terminal:

```
python src/streams_client.py [OPTIONS]
```

| Option | What it does |
|--------|-------------|
| `-p FILE_OR_URL` | Load a playlist on startup |
| `-s URL URL ...` | Open specific stream URLs directly |
| `-e` | Start with no streams (blank) |
| `--grid RxC` | Set grid size (e.g. `2x2`, `3x3`, or `dynamic`) |
| `--preset NAME` | Load a saved preset by name |
| `--fullscreen` | Start in fullscreen |
| `--no-audio` | Start muted |
| `--reset` | Reset all settings to defaults |
| `--list-presets` | Print saved presets and exit |
| `--list-favourites` | Print saved favourites and exit |
| `-v` | Show detailed logs in the terminal |

**Examples:**

```bash
# Watch two cameras side by side
python src/streams_client.py -s rtsp://cam1 rtsp://cam2 --grid 1x2

# Load a TV playlist
python src/streams_client.py -p https://example.com/tv.m3u8

# Start empty, add streams later from the UI
python src/streams_client.py -e
```

---

## Troubleshooting

| Problem | What to do |
|---------|-----------|
| "Failed to initialize VLC" | Make sure VLC is installed. Download it from https://www.videolan.org/vlc/ |
| Streams keep reconnecting | Your internet connection may be unstable. Try increasing the network buffer in *Preferences*. |
| No audio | Check the mute button and volume slider in the stream's control bar. Also make sure the stream is selected (green border). Only the selected stream plays audio. |
| Video stutters or drops frames | Try a lower quality (click the gear icon on the stream). Reduce the number of simultaneous streams. |
| macOS blocks the app | Right-click the `.app` > Open. You only need to do this once. |
| Windows SmartScreen warning | Click "More info" > "Run anyway". |

Detailed logs are saved in the `logs/` folder inside the app directory.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| [VLC](https://www.videolan.org/vlc/) | Media playback engine (install separately) |
| [PyQt6](https://pypi.org/project/PyQt6/) | User interface |
| [qasync](https://pypi.org/project/qasync/) | Bridges Python async with the Qt event loop |
| [python-vlc](https://pypi.org/project/python-vlc/) | Python bindings for VLC |
