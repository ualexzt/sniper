"""Snapshot public reference implementations; never loads trading credentials."""
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
import requests

FILES = ['utils.py', 'strategies/Strategy.py', 'services/candle_service.py',
         'indicators/bollinger_bands.py', 'indicators/keltner.py',
         'indicators/atr.py', 'indicators/adx.py', 'indicators/linearreg_slope.py',
         'indicators/ema.py', 'indicators/sma.py', 'indicators/ma.py']

if __name__ == '__main__':
    root = Path('research_sources')
    root.mkdir(exist_ok=True)
    rows = []
    for file in FILES:
        url = 'https://raw.githubusercontent.com/jesse-ai/jesse/master/jesse/' + file
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        path = root / file.replace('/', '__')
        path.write_bytes(response.content)
        rows.append(dict(url=url, path=str(path), sha256=hashlib.sha256(response.content).hexdigest()))
    (root / 'manifest.json').write_text(json.dumps(dict(retrieved_utc=datetime.now(timezone.utc).isoformat(), files=rows), indent=2))
    print(f'Snapshotted {len(rows)} source files', flush=True)
