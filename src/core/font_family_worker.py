"""Background worker for ConfigDialog's Appearance-tab font list.

Moves the fc-list subprocess call + QFontDatabase clustering (see
_compute_canonical_font_families's docstring below for why it's needed)
off the UI thread, so the first Settings-dialog open of a session isn't
blocked on it. ConfigDialog caches the result after the first run, so
this only actually executes once per process.
"""

import subprocess
from collections import defaultdict

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase

from src.common.cancellable_worker import CancellableWorker


class FontFamilyWorker(CancellableWorker):
    """Computes the alias-deduplicated font family set in the background."""

    computed = Signal(set)

    def run(self):
        families = self._compute_canonical_font_families()
        if not self.is_cancelled:
            self.computed.emit(families)

    def _compute_canonical_font_families(self) -> set:
        """Return the subset of QFontDatabase.families() that are real
        typefaces rather than fontconfig's named-instance aliases for a
        single weight/width of a variable font (e.g. "Noto Sans Thin" is
        the same physical font file as "Noto Sans", just pinned to one
        weight — fontconfig registers both as separate "family" names).

        Ground truth for "same file" comes from `fc-list`, which prints
        every family name a given font file is registered under on one
        line. Family names that co-occur on any line are unioned into a
        cluster (transitively, so e.g. "Noto Sans Canadian Aboriginal" and
        all its abbreviated weight aliases like "Noto Sans CanAborig XLt"
        land in one cluster even though the names don't share a textual
        prefix). Within each cluster only the name with the most styles()
        of its own is kept — that's the family Qt considers to have the
        full weight/width range, i.e. the non-aliased one. Genuinely
        distinct typefaces that just happen to share a name fragment
        (e.g. "Arial" / "Arial Black" are separate font files) never end
        up in the same cluster, so they're both kept.

        Falls back to the unfiltered family list if `fc-list` isn't on
        PATH (non-Linux, or fontconfig missing) — no worse than before.
        """
        all_families = set(QFontDatabase.families())
        if self.is_cancelled:
            return all_families
        try:
            result = subprocess.run(
                ["fc-list", ":", "family"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return all_families

        parent = {}

        def find(name):
            while parent[name] != name:
                parent[name] = parent[parent[name]]
                name = parent[name]
            return name

        for line in result.stdout.splitlines():
            group = [name.strip() for name in line.split(",") if name.strip()]
            for name in group:
                parent.setdefault(name, name)
            for name in group[1:]:
                root_a, root_b = find(group[0]), find(name)
                if root_a != root_b:
                    parent[root_a] = root_b

        clusters = defaultdict(list)
        for name in parent:
            clusters[find(name)].append(name)

        style_count = {}

        def styles_len(name):
            if name not in style_count:
                style_count[name] = len(QFontDatabase.styles(name))
            return style_count[name]

        canonical = {
            max(members, key=lambda n: (styles_len(n), -len(n)))
            for members in clusters.values()
        }
        canonical |= all_families - set(parent.keys())
        return canonical & all_families
