"""Small shared helpers for local pipeline records."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from copy import deepcopy


_IO_SESSION = ContextVar('asset_pipeline_io_session', default=None)


@contextmanager
def io_session():
    """Reuse verified reads only within one synchronous operation, never on disk."""
    if _IO_SESSION.get() is not None:
        yield
        return
    token = _IO_SESSION.set({})
    try:
        yield
    finally:
        _IO_SESSION.reset(token)


def io_operation(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with io_session():
            return function(*args, **kwargs)
    return wrapped


def operation_cache(name):
    session = _IO_SESSION.get()
    return session.setdefault(name, {}) if session is not None else None


def file_stamp(path):
    info = Path(path).stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_json(path):
    path = Path(path).resolve()
    before = file_stamp(path)
    cache = operation_cache('json')
    key = str(path)
    if cache is not None and cache.get(key, (None,))[0] == before:
        return deepcopy(cache[key][1])
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if file_stamp(path) != before:
        raise ValueError('JSON 文件在读取期间发生变化')
    if cache is not None:
        cache[key] = (before, deepcopy(value))
    return value


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def file_hash(path, *, refresh=False):
    path = Path(path).resolve()
    before = file_stamp(path)
    cache = operation_cache('hashes')
    key = str(path)
    if not refresh and cache is not None and cache.get(key, (None,))[0] == before:
        return cache[key][1]
    result = _read_file_hash(path)
    if file_stamp(path) != before:
        raise ValueError('文件在读取期间发生变化，请重试')
    if cache is not None:
        cache[key] = (before, result)
    return result


def object_hash(value):
    return hash_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8"))


def script_hash(request):
    if request.get("script_path"):
        return file_hash(Path(request["script_path"]).expanduser().resolve())
    return hash_bytes(request["script_text"].encode("utf-8"))


def atomic_json(path, value):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)
