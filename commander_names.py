"""Official commander display names extracted from Forts language resources."""
from __future__ import annotations

import json
from pathlib import Path
import re


def load_commander_names(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Load the checked-in code-to-name catalogue, failing closed on bad data."""
    source = path or Path(__file__).with_name('commander_names.json')
    try:
        document = json.loads(source.read_text(encoding='utf-8'))
        entries = document.get('commanders', {})
    except (OSError, ValueError, AttributeError, TypeError):
        return {}
    if not isinstance(entries, dict):
        return {}
    result = {}
    for log_code, entry in entries.items():
        if not isinstance(log_code, str) or not re.fullmatch(r'commander-[a-z0-9-]{1,80}', log_code):
            continue
        if not isinstance(entry, dict) or entry.get('log_code') != log_code:
            continue
        names = {language: name for language in ('ru', 'en')
                 if isinstance((name := entry.get(language)), str) and name.strip() and len(name) <= 80}
        if names:
            result[log_code] = names
    return result


COMMANDER_NAMES = load_commander_names()
