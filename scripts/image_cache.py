"""
Checksum tracking for image generation scripts.
Stores hash of each prompt so we only regenerate if content changed.
"""

import hashlib
import json
from pathlib import Path


def hash_prompt(text: str) -> str:
    """Hash a prompt string."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_checksums(cache_path: Path) -> dict[str, str]:
    """Load existing checksums. Returns {} if file doesn't exist."""
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_checksums(cache_path: Path, checksums: dict[str, str]) -> None:
    """Save checksums to file."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, 'w') as f:
        json.dump(checksums, f, indent=2)


def should_generate(
    key: str,
    prompt: str,
    checksums: dict[str, str],
    overwrite: bool,
) -> bool:
    """
    Decide whether to generate an image.

    Generate if:
    - overwrite flag is set, OR
    - prompt hash doesn't exist in checksums, OR
    - prompt hash differs from stored checksum
    """
    if overwrite:
        return True

    current_hash = hash_prompt(prompt)
    stored_hash = checksums.get(key)

    if stored_hash is None:
        return True  # new entry

    return current_hash != stored_hash  # changed entry
