"""Versioned stage checkpoints. No credentials, environment dumps or deletions."""
from __future__ import annotations

import hashlib
import json
import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from uuid import uuid4

LOGGER = logging.getLogger(__name__)


def digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def package_versions(*names):
    result = {}
    for name in names:
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = 'unavailable'
    return result


class StageCache:
    def __init__(self, root: Path, enabled=True):
        self.root = Path(root) / 'cache' / 'stages-v1'
        self.enabled = enabled

    def get(self, stage, key):
        if not self.enabled:
            return None
        directory = self.root / stage / key
        if not directory.exists():
            return None
        for path in sorted(directory.glob('*.json'), reverse=True):
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                if record['key'] == key and digest(record['payload']) == record['checksum']:
                    LOGGER.info('Reusing %s checkpoint %s', stage, key[:12])
                    return record['payload']
            except (OSError, ValueError, KeyError, TypeError):
                LOGGER.warning('Ignoring invalid checkpoint; file preserved: %s', path)
        return None

    def put(self, stage, key, payload):
        if not self.enabled:
            return
        directory = self.root / stage / key
        directory.mkdir(parents=True, exist_ok=True)
        # Exclusive new files preserve prior attempts, even after interruption.
        path = directory / f'{uuid4().hex}.json'
        record = dict(key=key, payload=payload, checksum=digest(payload))
        with path.open('x', encoding='utf-8') as output:
            json.dump(record, output, ensure_ascii=False, allow_nan=False)
