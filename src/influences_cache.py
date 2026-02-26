import json
from datetime import datetime
from pathlib import Path

from src.logger_config import logger


class GraphCache:
    def __init__(self):
        self.cache_dir = Path("config/graph_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_cache_path(self, cache_key):
        """Get file path for cache key"""
        # Sanitize cache key for filename
        safe_key = "".join(c for c in str(cache_key) if c.isalnum() or c in ("_", "-"))
        return self.cache_dir / f"{safe_key}.json"

    def save_layout(self, cache_key, nodes, positions, edges):
        """Save layout to cache"""
        try:
            cache_data = {
                "timestamp": datetime.now().isoformat(),
                "nodes": nodes,
                "positions": {
                    str(node_id): (pos.x(), pos.y())
                    for node_id, pos in positions.items()
                },
                "edges": edges,
            }
            with open(self.get_cache_path(cache_key), "w") as f:
                json.dump(cache_data, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving cache: {e}")

    def load_layout(self, cache_key):
        """Load layout from cache"""
        try:
            cache_file = self.get_cache_path(cache_key)
            if cache_file.exists():
                with open(cache_file, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading cache: {e}")
        return None

    def clear_all(self):
        """Clear all cached layouts"""
        try:
            for cache_file in self.cache_dir.glob("*.json"):
                cache_file.unlink()
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            raise
