"""Read-only, bytes-like view of an open audio file that reads only the regions a parser touches."""

from collections import OrderedDict
import os


class LazyFileView:
    """Supports len(), integer and slice indexing, and rfind(), like the bytes buffer the format readers expect."""

    # The readers walk headers and chunk tables, so most files touch only a few blocks.
    BLOCK_SIZE = 64 * 1024
    # LRU ceiling, so a scan over the whole stream (MP3 without a Xing header) keeps at most 4 MiB.
    MAX_CACHED_BLOCKS = 64

    def __init__(self, file_obj):
        """Wrap a binary file object opened for reading; the size is fixed when the view is made."""
        self._file = file_obj
        self._size = os.fstat(file_obj.fileno()).st_size
        self._blocks: OrderedDict[int, bytes] = OrderedDict()

    def __len__(self):
        """Return the file size in bytes."""
        return self._size

    def __getitem__(self, key):
        """Return one byte as an int, or a slice as bytes (fewer bytes if the file got shorter after open)."""
        if isinstance(key, slice):
            start, stop, step = key.indices(self._size)
            if step != 1:
                return bytes(self[i] for i in range(start, stop, step))
            return self._read(start, stop) if stop > start else b""

        index = key + self._size if key < 0 else key
        if not 0 <= index < self._size:
            raise IndexError("LazyFileView index out of range")
        block = self._block(index // self.BLOCK_SIZE)
        offset = index % self.BLOCK_SIZE
        if offset >= len(block):
            raise IndexError("LazyFileView index past the end of the file on disk")
        return block[offset]

    def rfind(self, sub, start=0, end=None):
        """Return the highest offset of sub within [start, end), or -1, scanning back one block at a time."""
        end = self._size if end is None else min(end, self._size)
        window_end = end
        while window_end > start:
            window_start = max(start, window_end - self.BLOCK_SIZE)
            # Extend past window_end so a match that crosses the window edge is still found.
            chunk = self._read(window_start, min(end, window_end + len(sub) - 1))
            found = chunk.rfind(sub)
            if found >= 0:
                return window_start + found
            window_end = window_start
        return -1

    def _read(self, start, stop):
        """Return the bytes in [start, stop); large ranges skip the block cache."""
        if stop - start >= self.BLOCK_SIZE:
            self._file.seek(start)
            return self._file.read(stop - start)

        first, last = start // self.BLOCK_SIZE, (stop - 1) // self.BLOCK_SIZE
        joined = b"".join(self._block(i) for i in range(first, last + 1))
        offset = first * self.BLOCK_SIZE
        return joined[start - offset : stop - offset]

    def _block(self, index):
        """Return block index from the LRU cache, reading it from the file on a miss."""
        block = self._blocks.get(index)
        if block is not None:
            self._blocks.move_to_end(index)
            return block

        self._file.seek(index * self.BLOCK_SIZE)
        block = self._file.read(self.BLOCK_SIZE)
        self._blocks[index] = block
        if len(self._blocks) > self.MAX_CACHED_BLOCKS:
            self._blocks.popitem(last=False)
        return block
