"""Cloud secret encryption; the key lives outside the project data volume."""
import os
from pathlib import Path


def cipher():
    from cryptography.fernet import Fernet
    path = os.environ.get("HELIX_SECRET_KEY_FILE")
    if not path:
        raise RuntimeError("HELIX_SECRET_KEY_FILE is required")
    key = Path(path).read_bytes().strip()
    return Fernet(key)


def protect(data):
    return "fernet", cipher().encrypt(data).decode("ascii")


def unprotect(blob):
    return cipher().decrypt(blob.encode("ascii"))
