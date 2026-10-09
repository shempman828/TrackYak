"""Background worker that computes ConfigDialog's alias-deduplicated font family list."""

# Runs fc-list + QFontDatabase clustering off the UI thread; ConfigDialog caches the result per process.

from collections import defaultdict
import subprocess

from PySide6.QtCore import Signal
from PySide6.QtGui import QFontDatabase

from src.common.cancellable_worker import CancellableWorker


class FontFamilyWorker(CancellableWorker):
    """Computes the alias-deduplicated font family set in the background."""

    computed = Signal(set)

    def run(self):
        """Compute the families and emit them unless cancelled."""
        families = self._compute_canonical_font_families()
        if not self.is_cancelled:
            self.computed.emit(families)

    def _compute_canonical_font_families(self) -> set:
        """Return QFontDatabase.families() minus fontconfig's per-weight/width aliases of variable fonts."""
        # fc-list prints every family name a font file is registered under on one line. Names that
        # share a line are unioned (transitively) into one cluster, and only the member with the most
        # styles() is kept. Distinct files that share a name fragment (Arial / Arial Black) stay apart.
        # Without fc-list (non-Linux, no fontconfig) the unfiltered list is returned.
        all_families = set(QFontDatabase.families())
        if self.is_cancelled:
            return all_families
        try:
            result = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError):
            return all_families

        parent = {}

        def find(name):
            """Return the cluster root of name, with path halving."""
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
            """Return the cached style count of a family."""
            if name not in style_count:
                style_count[name] = len(QFontDatabase.styles(name))
            return style_count[name]

        canonical = {max(members, key=lambda n: (styles_len(n), -len(n))) for members in clusters.values()}
        canonical |= all_families - set(parent.keys())
        return canonical & all_families
