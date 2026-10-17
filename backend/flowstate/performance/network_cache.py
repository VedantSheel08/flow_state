"""Reuse parsed networks while invalidating changes at the same file path."""
from functools import lru_cache
import hashlib
from pathlib import Path

from ..network.info import load_net_info


@lru_cache(maxsize=8)
def _parsed(path, network_id, network_digest, metadata_digest):
    # Bypass the legacy path-only cache. Content digests are part of our key.
    return load_net_info.__wrapped__(path, network_id)


def load_network(path: str, network_id: str):
    path = str(Path(path).resolve())
    metadata = Path(path).with_name(f"{network_id}.city.json")
    return _parsed(path, network_id, hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                   hashlib.sha256(metadata.read_bytes()).hexdigest() if metadata.exists() else None)


load_network.cache_clear = _parsed.cache_clear
load_network.cache_info = _parsed.cache_info
