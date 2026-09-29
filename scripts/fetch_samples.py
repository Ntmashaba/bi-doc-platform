"""Fetch only the pinned public acceptance corpus; verify bytes before use."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def fetch():
    cache = ROOT / 'samples/downloads'
    cache.mkdir(parents=True, exist_ok=True)
    for item in json.loads((ROOT/'samples/manifest.json').read_text()):
        path = cache/item['file']
        if not path.exists():
            request = urllib.request.Request(item['url'], headers={'User-Agent':'bi-doc-platform-acceptance'})
            with urllib.request.urlopen(request, timeout=120) as r:
                raw = r.read(100*1024*1024+1)
            if len(raw)>100*1024*1024:
                raise ValueError('Sample exceeds the 100 MiB download limit')
            if hashlib.sha256(raw).hexdigest()!=item['sha256']:
                raise ValueError(f"Checksum mismatch: {item['file']}")
            path.write_bytes(raw)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError(f"Checksum mismatch: {item['file']}")
        print('Verified',item['file'])
    return cache


if __name__=='__main__':
    fetch()
