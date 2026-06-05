"""Tests for content-addressed deduplication.

Per spec §5.4: same content → same SHA-256 → same stash. Reading the same
file twice (same session, different session, subagent) reuses the existing
stash. No duplicate bytes written.
"""

from src.store import StashStore, hash_content


def test_dedup_same_content_same_hash(store):
    """Two stashes of identical content produce the same hash."""
    content = b"def foo():\n    return 42\n"
    r1 = store.stash(content, tool="Read", args={"file_path": "foo.py"})
    r2 = store.stash(content, tool="Read", args={"file_path": "foo.py"})
    assert r1.sha256 == r2.sha256
    assert r1.id == r2.id


def test_dedup_does_not_duplicate_bytes(store):
    """Stashing the same content twice writes the content file only once."""
    content = b"x" * 1000
    store.stash(content, tool="Read", args={"file_path": "x.py"})
    store.stash(content, tool="Read", args={"file_path": "x.py"})

    # Exactly one .txt file with this hash
    matching = list(store.stash_dir.glob("*.txt"))
    assert len(matching) == 1


def test_dedup_appends_sighting(store):
    """Re-stashing the same content adds a sighting to the record."""
    content = b"def bar(): pass"
    r1 = store.stash(content, tool="Read", args={"file_path": "bar.py", "session": "s1"})
    r2 = store.stash(content, tool="Read", args={"file_path": "bar.py", "session": "s2"})

    # Both records should have 2 sightings
    assert len(r1.sightings) == 1
    assert len(r2.sightings) == 2
    sessions = {s["session"] for s in r2.sightings}
    assert sessions == {"s1", "s2"}


def test_dedup_different_content_different_hash(store):
    """Different content → different stashes."""
    r1 = store.stash(b"def a(): pass", tool="Read", args={"file_path": "a.py"})
    r2 = store.stash(b"def b(): pass", tool="Read", args={"file_path": "b.py"})
    assert r1.sha256 != r2.sha256
    assert r1.id != r2.id

    # Two .txt files exist
    assert len(list(store.stash_dir.glob("*.txt"))) == 2


def test_dedup_across_sessions_via_index(store):
    """The append-only index records every sighting, dedup-by-hash on read."""
    content = b"persistent content"
    store.stash(content, tool="Read", args={"file_path": "x.py", "session": "s1"})
    store.stash(content, tool="Bash", args={"cmd": "cat x.py", "session": "s2"})

    # Index has 2 lines (one per stash call), but only 1 unique hash
    with open(store.index_path) as f:
        lines = f.readlines()
    assert len(lines) == 2

    # list_all dedups by sha256, returns 1 record
    records = list(store.list_all())
    assert len(records) == 1
    assert records[0]["sha256"] == hash_content(content)


def test_dedup_read_returns_byte_exact(store):
    """Re-stashing preserves original bytes losslessly."""
    content = "Hello, 世界! \n\t Special chars: € ñ 🎉".encode("utf-8")
    store.stash(content, tool="Read", args={"file_path": "utf8.txt"})
    store.stash(content, tool="Read", args={"file_path": "utf8.txt"})
    read_back = store.read(hash_content(content))
    assert read_back == content


def test_dedup_short_id_uniqueness(store):
    """The 6-char short ID is enough to look up a stash."""
    content = b"unique content for short id test"
    r = store.stash(content, tool="Read", args={"file_path": "x.py"})

    # Look up by short ID
    record = store.get_record(r.id)
    assert record is not None
    assert record["sha256"] == r.sha256

    # Look up by full hash
    record = store.get_record(r.sha256)
    assert record is not None


def test_dedup_path_denylist_redirects_to_secret_dir(store):
    """Sensitive paths go to secret-stash/."""
    content = b"API_KEY=supersecret123"
    r = store.stash(
        content,
        tool="Read",
        args={"file_path": "/app/.env"},
        sensitive=True,
    )
    # The file is in secret-stash, not stash
    secret_path = store.secret_dir / f"{r.sha256}.txt"
    stash_path = store.stash_dir / f"{r.sha256}.txt"
    assert secret_path.exists()
    assert not stash_path.exists()
    assert r.sensitive is True
