"""
Content-addressed disk store for tool outputs.

Every stashed tool result is split across two files:
    <hash>.txt  - raw bytes (verbatim ground truth)
    <hash>.json - metadata: tool, args, summary, structure, sightings

Plus an append-only index.jsonl for fast lookup by hash, tool, or file path.

Deduplication is automatic: same content → same SHA-256 → same stash.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator


# --- Path denylist for sensitive content ------------------------------------

SENSITIVE_PATH_PATTERNS = (
    ".env",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    ".pem",
    ".key",
    ".p12",
    "secrets.yml",
    "secrets.yaml",
)


def is_sensitive_path(path: str) -> bool:
    """Return True if a path looks like it may contain secrets."""
    name = os.path.basename(path).lower()
    for pattern in SENSITIVE_PATH_PATTERNS:
        if pattern in name:
            return True
    return False


# --- Data classes ------------------------------------------------------------

@dataclass
class StashRecord:
    """Metadata for a single stashed tool output."""
    id: str                          # first 6 hex of sha256
    sha256: str                      # full hex digest
    created_at: str                  # ISO 8601
    size_bytes: int
    size_tokens: int                 # approximate, char/4
    tool: str
    args: dict[str, Any]
    structure: dict[str, Any]        # exports/imports/shape, language, etc.
    summary: str                     # one-line summary (≤50 tokens)
    stub_depth: str = "outline"      # minimal | outline | full-toc
    binary: bool = False
    secret_redacted: bool = False
    sensitive: bool = False          # stashed in secret-stash/ tree
    sightings: list[dict[str, str]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- Token estimation --------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough token estimate. char/4 is close enough for budgeting."""
    if not text:
        return 0
    return max(1, len(text) // 4)


# --- Content hashing ---------------------------------------------------------

def hash_content(content: bytes) -> str:
    """Return the full hex SHA-256 of the content."""
    return hashlib.sha256(content).hexdigest()


def short_id(full_hash: str, n: int = 6) -> str:
    """Return the first n hex chars of a hash for display."""
    return full_hash[:n]


# --- Store -------------------------------------------------------------------

class StashStore:
    """
    Content-addressed store for tool outputs.

    Layout:
        <root>/
            stash/         - normal content
                <hash>.txt
                <hash>.json
            secret-stash/  - content from sensitive paths (mode 0700)
                <hash>.txt
                <hash>.json
            index.jsonl    - append-only, written under file lock
    """

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).expanduser()
        self.stash_dir = self.root / "stash"
        self.secret_dir = self.root / "secret-stash"
        self.index_path = self.root / "index.jsonl"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.stash_dir.mkdir(parents=True, exist_ok=True)
        self.secret_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secret_dir, 0o700)
        if not self.index_path.exists():
            self.index_path.touch()
            os.chmod(self.index_path, 0o600)

    # -- writing --

    def stash(
        self,
        content: bytes,
        *,
        tool: str,
        args: dict[str, Any] | None = None,
        structure: dict[str, Any] | None = None,
        summary: str = "",
        stub_depth: str = "outline",
        sensitive: bool = False,
    ) -> StashRecord:
        """
        Write content to the store. Returns the metadata record.

        If content is already stashed (by hash), returns the existing record
        with a new sighting appended. No duplicate bytes written.
        """
        full_hash = hash_content(content)
        short = short_id(full_hash)
        # Decide where to write
        target_dir = self.secret_dir if sensitive else self.stash_dir
        content_path = target_dir / f"{full_hash}.txt"
        meta_path = target_dir / f"{full_hash}.json"

        existing = self._read_existing_record(meta_path)
        if existing is not None:
            # Dedup: append a sighting, return updated record
            existing.sightings.append(
                {"session": args.get("session", "unknown") if args else "unknown",
                 "ts": _now_iso()}
            )
            self._write_json(meta_path, existing.to_dict())
            self._append_index(existing.to_dict())
            return existing

        # New content - write files
        binary = _looks_binary(content)
        if binary:
            content_path = content_path.with_suffix(".bin")

        # Write atomically: write to temp, rename
        tmp_path = content_path.with_suffix(content_path.suffix + ".tmp")
        tmp_path.write_bytes(content)
        tmp_path.rename(content_path)

        # Try to lock down the secret-stash content
        if sensitive:
            os.chmod(content_path, 0o600)

        # Build and write metadata
        text = content.decode("utf-8", errors="replace") if not binary else ""
        record = StashRecord(
            id=short,
            sha256=full_hash,
            created_at=_now_iso(),
            size_bytes=len(content),
            size_tokens=estimate_tokens(text) if not binary else 0,
            tool=tool,
            args=args or {},
            structure=structure or {},
            summary=summary,
            stub_depth=stub_depth,
            binary=binary,
            sensitive=sensitive,
            sightings=[{"session": (args or {}).get("session", "unknown"),
                        "ts": _now_iso()}],
        )
        self._write_json(meta_path, record.to_dict())
        if sensitive:
            os.chmod(meta_path, 0o600)
        self._append_index(record.to_dict())
        return record

    # -- reading --

    def read(self, stash_id: str, *, allow_secret: bool = True) -> bytes | None:
        """
        Read raw content by stash ID. Returns None if not found.
        Tries both normal and secret-stash trees.
        """
        record = self.get_record(stash_id, allow_secret=allow_secret)
        if record is None:
            return None
        suffix = ".bin" if record["binary"] else ".txt"
        # Try both trees
        for base in (self.secret_dir, self.stash_dir):
            path = base / f"{record['sha256']}{suffix}"
            if path.exists():
                return path.read_bytes()
        return None

    def get_record(self, stash_id: str, *, allow_secret: bool = True) -> dict[str, Any] | None:
        """Return the metadata record for a stash ID, or None."""
        # If a full hash is given, try that first
        candidates: list[Path] = []
        if len(stash_id) >= 64:
            candidates.append(self.stash_dir / f"{stash_id}.json")
            if allow_secret:
                candidates.append(self.secret_dir / f"{stash_id}.json")
        # Otherwise scan both trees for a matching short ID
        for base in (self.stash_dir, self.secret_dir):
            if not base.exists():
                continue
            for path in base.glob(f"{stash_id}*.json"):
                if allow_secret or base != self.secret_dir:
                    try:
                        with open(path) as f:
                            return json.load(f)
                    except (json.JSONDecodeError, OSError):
                        continue
        # Full hash fallback
        for c in candidates:
            if c.exists():
                try:
                    with open(c) as f:
                        return json.load(f)
                except (json.JSONDecodeError, OSError):
                    return None
        return None

    def exists(self, stash_id: str) -> bool:
        return self.get_record(stash_id) is not None

    # -- listing --

    def list_all(
        self,
        *,
        tool: str | None = None,
        file_path: str | None = None,
        since: str | None = None,
        tags: list[str] | None = None,
        include_secret: bool = False,
    ) -> Iterator[dict[str, Any]]:
        """
        Iterate over records in the index, optionally filtered.
        The index is append-only and may have duplicates for the same hash
        (one per sighting); we dedup by sha256 and return the latest.
        """
        if not self.index_path.exists():
            return
        seen: dict[str, dict[str, Any]] = {}
        with open(self.index_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if include_secret is False and rec.get("sensitive"):
                    continue
                if tool and rec.get("tool") != tool:
                    continue
                if file_path and rec.get("args", {}).get("file_path") != file_path:
                    continue
                if since and rec.get("created_at", "") < since:
                    continue
                if tags:
                    rec_tags = set(rec.get("tags", []))
                    if not all(t in rec_tags for t in tags):
                        continue
                # Latest wins
                seen[rec["sha256"]] = rec
        yield from seen.values()

    def list_count(self) -> int:
        """Count unique stashes."""
        return sum(1 for _ in self.list_all())

    # -- internals --

    def _read_existing_record(self, meta_path: Path) -> StashRecord | None:
        if not meta_path.exists():
            return None
        try:
            with open(meta_path) as f:
                data = json.load(f)
            return StashRecord(**data)
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        tmp.rename(path)

    def _append_index(self, record: dict[str, Any]) -> None:
        # Append under file lock for safety
        with open(self.index_path, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(record, sort_keys=True) + "\n")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# --- helpers -----------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _looks_binary(content: bytes) -> bool:
    """Sniff for binary content. First 8KB is enough for a heuristic."""
    sample = content[:8192]
    if not sample:
        return False
    # NUL byte is a strong indicator
    if b"\x00" in sample:
        return True
    # >30% non-printable non-whitespace chars
    text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
    non_text = sum(1 for b in sample if b not in text_chars)
    return (non_text / len(sample)) > 0.30
