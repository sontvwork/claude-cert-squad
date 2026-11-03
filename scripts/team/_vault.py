"""Pack, seal and unpack a member's learner state for multi-device sync.

The state is CLAUDE-CERTIFICATION*.md plus learning-artifacts/. It travels as
a gzip tarball sealed with the member's sync_key: an HMAC-SHA256 keystream in
counter mode, then an HMAC-SHA256 tag over the ciphertext (encrypt-then-MAC,
RFC 2104 HMAC). The server and anyone who can view the Sheet see ciphertext.
"""

import base64
import hashlib
import hmac
import io
import os
import re
import secrets
import tarfile
from pathlib import PurePosixPath

PROGRESS_FILE = "CLAUDE-CERTIFICATION.md"
ARCHIVE_PATTERN = re.compile(r"^CLAUDE-CERTIFICATION-[A-Za-z0-9._-]+\.md$")
ARTIFACTS_DIR = "learning-artifacts"
SKIP_DIRS = {"__pycache__", "node_modules", ".venv", "venv", ".git", ".pytest_cache", ".mypy_cache"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
SKIP_SUFFIXES = (".pyc", ".pyo")
MAX_PACKED_BYTES = 1_000_000
MAGIC = b"CS1"
NONCE_BYTES = 16
TAG_BYTES = 32
BLOCK_BYTES = 32


class VaultError(ValueError):
    """The snapshot cannot be opened: wrong key, damaged data or an unsafe path."""


def new_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def key_bytes(key):
    text = str(key or "").strip()
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError as error:
        raise VaultError("sync_key không hợp lệ.") from error
    if len(raw) != 32:
        raise VaultError("sync_key không hợp lệ.")
    return raw


def derive(key, label):
    return hmac.new(key_bytes(key), label.encode("ascii"), hashlib.sha256).digest()


def auth_token(key):
    """What the server checks. It stores only the SHA-256 of this value."""
    return derive(key, "auth").hex()


def is_synced_path(path):
    parts = PurePosixPath(path).parts
    if not parts or any(part in ("", ".", "..") for part in parts) or PurePosixPath(path).is_absolute():
        return False
    if len(parts) == 1:
        return parts[0] == PROGRESS_FILE or bool(ARCHIVE_PATTERN.match(parts[0]))
    if parts[0] != ARTIFACTS_DIR or parts[-1] in SKIP_FILES or parts[-1].endswith(SKIP_SUFFIXES):
        return False
    return not any(part in SKIP_DIRS for part in parts[1:-1])


def collect(root):
    """Map every synced file under root to its bytes, keyed by POSIX path."""
    files = {}
    for entry in sorted(root.iterdir()) if root.is_dir() else []:
        if entry.is_file() and not entry.is_symlink() and is_synced_path(entry.name):
            files[entry.name] = entry.read_bytes()
    artifacts = root / ARTIFACTS_DIR
    if artifacts.is_dir():
        for folder, dirs, names in os.walk(artifacts):
            dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
            for name in sorted(names):
                path = os.path.join(folder, name)
                relative = PurePosixPath(os.path.relpath(path, root).replace(os.sep, "/")).as_posix()
                if not os.path.islink(path) and is_synced_path(relative):
                    with open(path, "rb") as handle:
                        files[relative] = handle.read()
    return files


def fingerprint(files):
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8") + b"\0" + hashlib.sha256(files[path]).digest())
    return digest.hexdigest()


def pack(files):
    buffer = io.BytesIO()
    # mtime 0 and sorted names keep the archive the same for the same files.
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=9) as archive:
        for path in sorted(files):
            info = tarfile.TarInfo(path)
            info.size = len(files[path])
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(files[path]))
    return buffer.getvalue()


def fit(files, limit=MAX_PACKED_BYTES):
    """Return (kept, dropped, packed). Drop the largest artifacts until the archive fits; progress files stay."""
    kept = dict(files)
    dropped = []
    packed = pack(kept)
    while len(packed) > limit:
        artifacts = [path for path in kept if path.startswith(ARTIFACTS_DIR + "/")]
        if not artifacts:
            raise VaultError(f"File tiến độ nén lại vẫn lớn hơn {limit // 1000}KB.")
        largest = max(artifacts, key=lambda path: (len(kept[path]), path))
        dropped.append(largest)
        del kept[largest]
        packed = pack(kept)
    return kept, dropped, packed


def unpack(packed):
    files = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(packed), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile() or not is_synced_path(member.name):
                    raise VaultError(f"Snapshot chứa đường dẫn không hợp lệ: {member.name!r}.")
                files[member.name] = archive.extractfile(member).read()
    except (tarfile.TarError, OSError, EOFError) as error:
        raise VaultError("Snapshot bị hỏng.") from error
    return files


def _keystream_xor(key, nonce, data):
    stream_key = derive(key, "enc")
    blocks = (len(data) + BLOCK_BYTES - 1) // BLOCK_BYTES
    stream = b"".join(
        hmac.new(stream_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest() for counter in range(blocks)
    )[: len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big") if data else b""


def _tag(key, email, nonce, ciphertext):
    return hmac.new(derive(key, "mac"), MAGIC + email.encode("utf-8") + b"\0" + nonce + ciphertext, hashlib.sha256).digest()


def seal(key, email, packed, nonce=None):
    nonce = nonce or secrets.token_bytes(NONCE_BYTES)
    ciphertext = _keystream_xor(key, nonce, packed)
    blob = MAGIC + nonce + ciphertext + _tag(key, email, nonce, ciphertext)
    return base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")


def open_sealed(key, email, text):
    try:
        blob = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError as error:
        raise VaultError("Snapshot bị hỏng.") from error
    if len(blob) < len(MAGIC) + NONCE_BYTES + TAG_BYTES or not blob.startswith(MAGIC):
        raise VaultError("Snapshot bị hỏng.")
    nonce = blob[len(MAGIC) : len(MAGIC) + NONCE_BYTES]
    ciphertext, tag = blob[len(MAGIC) + NONCE_BYTES : -TAG_BYTES], blob[-TAG_BYTES:]
    if not hmac.compare_digest(tag, _tag(key, email, nonce, ciphertext)):
        raise VaultError("Không mở được snapshot: sync_key không khớp hoặc dữ liệu bị sửa.")
    return _keystream_xor(key, nonce, ciphertext)
