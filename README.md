# TrackYak

TrackYak is a desktop music library manager for power users obsessed with metadata, statistics, and meticulous library building.

Most local players treat your library as a flat list of files with ID3 tags. Streaming apps treat it as an algorithm to keep you hooked. TrackYak treats it as a catalog — every track resolved against MusicBrainz, connected to its genre, mood, lyrics, chart history, and the artists who influenced it, and scored on actual audio quality rather than just bitrate. It's the difference between a folder of files and a collection you actually understand.

## Who it's for

- **Collectors and archivists** with a local FLAC/lossless library who want it cataloged properly instead of guessed at by a tagger
- **Audiophiles** who want objective playback-quality metrics (transient response, coherence, liveness) and an equalizer, not just a play button
- **Music nerds** who want to explore *why* a recording sounds the way it does — artist influence graphs, place mapping, awards, moods — not just play it
- **Chart completists** working from Billboard history who want to find and fill the gaps in their collection

If you want a library that lives entirely in the cloud, or a bare-bones player with zero metadata opinions, TrackYak is more tool than you need. If you've ever manually fixed an artist credit because MusicBrainz had it slightly wrong, it's built for you.

## Features

- **Library management** — track, album, artist, and playlist views backed by a local SQLite database
- **MusicBrainz integration** — metadata lookup/import, alias splitting/merging, and reverse-lookup awards import for tracks, albums, and artists
- **Audio fingerprinting** — AcoustID/chromaprint-based matching for duplicate detection and identification
- **Audiophile playback analysis** — perceptual audio metrics (transient response, coherence, liveness) and a built-in equalizer
- **Rich metadata** — genres, moods, lyrics, influences, places, awards, and Billboard chart recommendations/matching
- **Visualization** — artist influence graphs and place/map views
- **Device sync** — sync curated playlists to MTP devices (phones, DAPs) directly from your library

## Requirements

- Python 3.10+
- The native `chromaprint` library, for audio fingerprinting:
  - Debian/Ubuntu: `apt install libchromaprint1`
  - macOS: `brew install chromaprint`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running

```bash
python run.py
```

On first launch you'll be prompted to configure your music library location; the app creates its SQLite database and required asset directories automatically. Startup runs an environment check (Python version, required packages, fingerprinting backend) and fails fast with an actionable message if something's missing.

## Testing

```bash
pytest
```

Qt tests run headless via `QT_QPA_PLATFORM=offscreen` (configured in `tests/conftest.py`).

## License

TrackYak is licensed under the [TrackYak Public License 1.0](license.md) — free for personal and educational use; commercial use requires written permission from Baby Yak Studios.
