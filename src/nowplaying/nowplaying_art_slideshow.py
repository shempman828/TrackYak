"""
nowplaying_art_slideshow.py

Album-art/backdrop slideshow for NowPlayingView: gathering every available
art image for a track (front/rear/liner covers + artist photos), cycling
through them on a timer, and crossfading the small art card and the
full-window blurred backdrop.
"""

from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QPixmap

from src.album.album_art_worker import ArtCacheWorker
from src.image.artwork_cache import get_artwork_cache

# Art slideshow dwell times. Album covers get more screen time than artist
# photos so the cover art still dominates the rotation. Both are long enough
# to actually read a credit line / take in a photo, not just flash by.
_COVER_DWELL_MS = 6_000
_ARTIST_DWELL_MS = 5_000

# When a front cover is present, it's interleaved between every other image
# (front, rear, front, liner, front, artist-photo, ...) rather than shown as
# one single slide, so it never gets rotated away for long and doesn't turn
# into one giant multi-minute freeze on a track with many contributors. Each
# time it's shown, its dwell is set relative to the *next* image's dwell so
# the front cover ends up with this fraction of total cycle time no matter
# how many secondary images are in the rotation.
_FRONT_COVER_SHARE = 0.8

# Duration of the crossfade between successive art-slideshow images.
_ART_TRANSITION_MS = 950


class NowPlayingArtMixin:
    """
    Expects the host class to provide: self.track, self.default_art_path,
    self._current_pixmap, self._fade_anim, self._art_transition_anim,
    self._art_images, self._art_has_front, self._art_slide_idx,
    self._art_slide_timer, self._art_worker, self._art_generation,
    self._art_card, self._backdrop, and to be a QWidget subclass.
    """

    # ── art ───────────────────────────────────────────────────────────────

    def _load_art(self, pixmap: Optional[QPixmap]):
        """Single-image path kept for clearUI / fallback use."""
        self._start_art_slideshow([(pixmap, False, None)] if pixmap else [])

    def _load_art_from_track(self, track):
        """Build slideshow from all available art images for this track."""
        self._cancel_art_worker()
        self._art_generation += 1
        gen = self._art_generation

        album = getattr(track, "album", None)
        is_explicit = bool(getattr(album, "art_is_explicit", False)) if album else False
        cache = get_artwork_cache()

        # (pixmap, is_artist_photo, label) — artist photos are rendered
        # without forcing a square crop, since they aren't necessarily
        # square, and carry the artist's name to caption the photo.
        pixmaps: List[Tuple[QPixmap, bool, Optional[str]]] = []
        has_front = False
        if album and cache:
            # peek_has_art never reads/decodes the audio file. If the cache
            # row is confirmed valid, front/rear/liner are all safe to fetch
            # synchronously (get_pixmap warms all three roles in one pass,
            # so a hit on "front" means the others are cache hits too). If
            # it's unknown (cold cache/stale mtime), resolve it on a
            # background thread instead of blocking the UI on a file decode.
            known = cache.peek_has_art(album, "front")
            if known is None:
                self._start_art_worker(album, gen, track)
            else:
                for role in ("front", "rear", "liner"):
                    px = cache.get_pixmap(album, role, is_explicit)
                    if not px.isNull():
                        pixmaps.append((px, False, None))
                        if role == "front":
                            has_front = True

        # Also try artist-level image
        album_artist_ids = {
            a.artist_id for a in (getattr(album, "album_artists", None) or [])
        }
        for artist in getattr(track, "artists", None) or []:
            p = getattr(artist, "profile_pic_path", None) or ""
            if p and Path(p).exists():
                px = QPixmap(str(p))
                if not px.isNull():
                    name = getattr(artist, "artist_name", None) or None
                    if name and artist.artist_id not in album_artist_ids:
                        credit_roles = []
                        for ar in getattr(track, "artist_roles", None) or []:
                            if ar.artist_id != artist.artist_id:
                                continue
                            role_name = getattr(ar.role, "role_name", None)
                            if role_name and role_name not in (
                                "Primary Artist",
                                "Album Artist",
                            ) and role_name not in credit_roles:
                                credit_roles.append(role_name)
                        if credit_roles:
                            name = f"{name} ({', '.join(credit_roles)})"
                    pixmaps.append((px, True, name))

        if not pixmaps:
            default = self.default_art_path
            if default and Path(default).exists():
                px = QPixmap(default)
                if not px.isNull():
                    pixmaps.append((px, False, None))

        self._start_art_slideshow(pixmaps, has_front=has_front)

    def _cancel_art_worker(self):
        if self._art_worker is not None:
            self._art_worker.request_cancel()
            self._art_worker.wait()
            self._art_worker = None

    def _start_art_worker(self, album, gen: int, track):
        cache = get_artwork_cache()
        self._art_worker = ArtCacheWorker([album], cache, "front")
        self._art_worker.resolved.connect(
            lambda _album_id, g=gen, t=track: self._on_art_resolved(g, t)
        )
        self._art_worker.start()

    def _on_art_resolved(self, gen: int, track):
        """Cache row for `track`'s album is now warm - re-run the (now
        synchronous/cheap) art lookup, but only if nothing has superseded
        this request in the meantime."""
        if gen != self._art_generation or track is not self.track:
            return
        self._load_art_from_track(track)

    def _start_art_slideshow(
        self,
        pixmaps: List[Tuple[QPixmap, bool, Optional[str]]],
        has_front: bool = False,
    ):
        """Begin cycling through the given list of (pixmap, is_artist, label)
        triples. The blurred backdrop stays pinned to the album art (the
        first non-artist image) for the whole track; only the small art
        card rotates through the full set, artist photos included.

        When a front cover is present, the rotation interleaves it between
        every other image (front, rear, front, liner, front, artist, ...)
        instead of visiting it once per lap - see _dwell_for_index.
        """
        self._art_slide_timer.stop()

        first = pixmaps[0] if pixmaps else (None, False, None)
        backdrop_pixmap = next((px for px, is_artist, _ in pixmaps if not is_artist), first[0])

        if has_front and len(pixmaps) > 1:
            front, secondaries = pixmaps[0], pixmaps[1:]
            sequence = []
            for img in secondaries:
                sequence.append(front)
                sequence.append(img)
        else:
            sequence = pixmaps

        self._art_images = sequence
        self._art_has_front = has_front
        self._art_slide_idx = 0

        self._apply_art(*first)
        self._apply_backdrop(backdrop_pixmap)

        if len(sequence) > 1:
            self._art_slide_timer.setInterval(self._dwell_for_index(0))
            self._art_slide_timer.start()

    def _advance_art_slide(self):
        if not self._art_images:
            return
        self._art_slide_idx = (self._art_slide_idx + 1) % len(self._art_images)
        current = self._art_images[self._art_slide_idx]
        self._apply_art(*current)
        self._art_slide_timer.setInterval(self._dwell_for_index(self._art_slide_idx))

    def _dwell_for_index(self, idx: int) -> int:
        """How long the image at `idx` should stay on screen.

        Normally each image just dwells for its per-type duration. When a
        front cover is in the rotation, it occupies every even slot
        (front/secondary/front/secondary/...) - each visit's dwell is set
        relative to the secondary image right after it, so the front cover
        gets _FRONT_COVER_SHARE of cycle time regardless of how many
        secondary images there are, without any single slide ballooning.
        """
        _, is_artist, _ = self._art_images[idx]
        base = _ARTIST_DWELL_MS if is_artist else _COVER_DWELL_MS
        if not self._art_has_front or len(self._art_images) <= 1:
            return base
        is_front_slot = idx % 2 == 0
        if not is_front_slot:
            return base
        next_idx = (idx + 1) % len(self._art_images)
        _, next_is_artist, _ = self._art_images[next_idx]
        neighbor = _ARTIST_DWELL_MS if next_is_artist else _COVER_DWELL_MS
        return round(neighbor * _FRONT_COVER_SHARE / (1 - _FRONT_COVER_SHARE))

    def _apply_art(
        self, pixmap: Optional[QPixmap], is_artist: bool = False, label: Optional[str] = None
    ):
        """Push a single pixmap to the small art card with a crossfade."""
        self._current_pixmap = pixmap
        self._art_card.set_art(pixmap, is_artist, label)

        if self._art_transition_anim:
            self._art_transition_anim.stop()

        self._art_transition_anim = QPropertyAnimation(self._art_card, b"transitionProgress")
        self._art_transition_anim.setDuration(_ART_TRANSITION_MS)
        self._art_transition_anim.setStartValue(0.0)
        self._art_transition_anim.setEndValue(1.0)
        self._art_transition_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._art_transition_anim.start()

    def _apply_backdrop(self, pixmap: Optional[QPixmap]):
        """Crossfade the full-window blurred backdrop to `pixmap`. Called
        once per track (or when a new backdrop candidate appears), not on
        every art-card slide."""
        if self._fade_anim:
            self._fade_anim.stop()

        self._backdrop.set_pixmap(pixmap)
        self._backdrop._opacity = 0.0

        self._fade_anim = QPropertyAnimation(self._backdrop, b"backdropOpacity")
        self._fade_anim.setDuration(_ART_TRANSITION_MS)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._fade_anim.start()
