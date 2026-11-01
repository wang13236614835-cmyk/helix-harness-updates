"""Create fresh cloud secrets. Prints no keys; writes an owner-only setup-code file."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
from cryptography.fernet import Fernet

p = argparse.ArgumentParser()
p.add_argument('--secrets', required=True)
p.add_argument('--setup-code-file', required=True)
a = p.parse_args()
folder, codefile = Path(a.secrets), Path(a.setup_code_file)
folder.mkdir(parents=True, exist_ok=True)
if any(folder.iterdir()) or codefile.exists():
    raise SystemExit('Refusing to replace existing credentials')
code = secrets.token_urlsafe(32)
for target, value in [(folder / 'encryption.key', Fernet.generate_key()),
                      (folder / 'access.json', json.dumps({'bootstrap_hash': hashlib.sha256(code.encode()).hexdigest()}).encode()),
                      (codefile, (code + '\n').encode())]:
    with target.open('xb') as f:
        f.write(value)
    if os.name != 'nt':
        target.chmod(0o600)
print('Initialized. Open the setup-code file privately; do not include it in a ZIP.')
