"""Owned workspace, durable state, and read-only episode history."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from datetime import datetime, timezone

MODE = "narrated-short"
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
CHANNEL = re.compile(r"UC[-_A-Za-z0-9]{22}")
VIDEO_ID = re.compile(r"[-_A-Za-z0-9]{11}")
STUDIO = "https://studio.youtube.com"
HISTORY = re.compile(r"\n~~~json\n(.*?)\n~~~\n\Z", re.S)
FONT = Path(os.environ.get("ROCK_KANIT_FONT", Path.home() / "Library/Fonts/Kanit-Bold.ttf")).expanduser().resolve()


class Problem(ValueError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(path, value):
    data = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            if hasattr(fcntl, "F_FULLFSYNC"):
                fcntl.fcntl(handle.fileno(), fcntl.F_FULLFSYNC)
            os.replace(temporary, path)
            sync_dir(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def read(path):
    if path.is_symlink() or not path.is_file():
        raise Problem(f"Expected a regular file at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def file_hash(path):
    if path.is_symlink() or not path.is_file():
        raise Problem(f"Expected a regular file at {path}")
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def key_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def stamp(path):
    if path.is_symlink() or not path.is_file():
        raise Problem(f"Expected a regular file at {path}")
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def slug(value):
    if not isinstance(value, str) or len(value) > 80 or not SLUG.fullmatch(value):
        raise Problem("Use a lowercase ASCII ID with digits or hyphens, up to 80 characters.")
    return value


def directory(path, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise Problem(f"Expected a real directory at {path}")
    return path


class Workspace:
    def __init__(self, project):
        if not project.is_dir():
            raise Problem("Create the project directory before running the CLI.")
        self.project = project.resolve()
        # Each workflow has its own state even when pointed at the same project.
        base = directory(self.project / ".rock-spoiler-work", create=True)
        self.base = directory(base / MODE, create=True)
        self.episodes = directory(self.base / "episodes", create=True)
        self.channel_file = self.base / "channel.json"
        self.profile = self.base / "chrome-profile"

    @contextlib.contextmanager
    def lock(self):
        with os.fdopen(os.open(self.base / "workspace.lock",
                              os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Problem("A command already holds this workspace. Check status and retry when it finishes.") from None
            yield

    def channel(self):
        value = read(self.channel_file).get("channel_id") if self.channel_file.exists() else None
        if not isinstance(value, str) or not CHANNEL.fullmatch(value):
            raise Problem("Run login and setup to bind your YouTube Studio channel.")
        return value

    def episode(self, identifier):
        return Episode(self, slug(identifier))

    def existing(self):
        for path in sorted(self.episodes.iterdir()):
            if SLUG.fullmatch(path.name):
                yield self.episode(path.name)


class Episode:
    def __init__(self, workspace, identifier):
        self.workspace = workspace
        self.id = identifier
        self.path = workspace.episodes / identifier
        self.work = self.path / "work"
        self.history_path = self.path / "README.md"

    def history(self):
        directory(self.path)
        if not self.history_path.exists() and not self.history_path.is_symlink():
            return None
        if self.history_path.is_symlink() or not self.history_path.is_file():
            raise Problem("Episode history must be a regular README.md.")
        found = HISTORY.search(self.history_path.read_text(encoding="utf-8"))
        if not found:
            raise Problem("Episode README lacks a valid receipt block.")
        data = json.loads(found[1])
        check_history(data, self.id)
        return data

    def job(self):
        directory(self.path)
        directory(self.work)
        data = read(self.work / "job.json")
        if data.get("episode") != self.id or data.get("mode") != MODE or not re.fullmatch(
                r"[0-9a-f]{32}", data.get("job", "")):
            raise Problem("Episode ownership marker is invalid.")
        return data

    def active(self):
        if self.history():
            raise Problem("This episode has a saved Private receipt. Use status or finalize.")
        return self.job()

    def receipt(self):
        self.job()
        path = self.work / "upload.json"
        return read(path) if path.exists() or path.is_symlink() else {}

    def change(self, **changes):
        data = {**self.receipt(), **changes, "updated_at": utc()}
        write(self.work / "upload.json", data)
        return data


def check_history(data, identifier):
    if (data.get("episode") != identifier or data.get("mode") != MODE
            or data.get("status") != "saved_private" or data.get("stage") != "verified"
            or data.get("visibility") != "private" or not CHANNEL.fullmatch(data.get("channel_id", ""))
            or not VIDEO_ID.fullmatch(data.get("video_id", ""))
            or data.get("studio_url") != f"{STUDIO}/video/{data.get('video_id')}/edit"
            or not re.fullmatch(r"[0-9a-f]{32}", data.get("job", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", data.get("mp4_sha256", ""))
            or not data.get("verified_at") or not data.get("title") or not data.get("premise")
            or not isinstance(data.get("work_identity"), list) or len(data["work_identity"]) != 2
            or not isinstance(data.get("story"), dict) or not data.get("art") or not data.get("gate")):
        raise Problem("Receipt does not identify this episode and a verified saved Private video.")
    return data


def remove_work(episode, identity):
    """Open through real directories and remove only the package-owned work tree."""
    if not shutil.rmtree.avoids_symlink_attacks:
        raise Problem("This Python lacks symlink-safe directory removal.")
    if not episode.work.exists() and not episode.work.is_symlink():
        return
    # The README is written only after checking the marker. During interrupted
    # removal job.json may already be gone, so the validated README is the journal.
    if episode.work.is_symlink() or not episode.work.is_dir():
        raise Problem("Refusing to remove a replaced episode work directory.")
    if [episode.work.stat().st_dev, episode.work.stat().st_ino] != identity:
        raise Problem("Episode work directory was replaced after finalization began.")
    fd = os.open(episode.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.stat("work", dir_fd=fd, follow_symlinks=False)
        if [current.st_dev, current.st_ino] != identity:
            raise Problem("Episode work directory identity changed during cleanup.")
        shutil.rmtree("work", dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)
