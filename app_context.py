from __future__ import annotations

import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib


DEBUG = os.getenv("DOGEAR_DEBUG") == "1"


def dlog(msg: str) -> None:
    """Emit a debug line when DOGEAR_DEBUG=1."""
    if not DEBUG:
        return
    try:
        line = f"[dogear] {msg}\n"
        sys_stderr = getattr(__import__("sys"), "stderr")
        sys_stderr.write(line)
        sys_stderr.flush()
        with open("/tmp/dogear.log", "a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        # Debug logging must never be able to crash the app.
        pass


APP_ID = "io.github.jessemcgowan.DogEar"
APP_NAME = "Dog Ear"
APP_SLUG = "DogEar"
APP_VERSION = "0.1.0"


COMPLETED_DIRNAME = f"{APP_SLUG}_Completed"
TEXTPAGES_DIRNAME = f"{APP_SLUG}_TextPages"


def _share_root(app_id: str) -> str:
    return f"/app/share/{app_id}"


def _xdg(app_slug: str, kind: str, *parts: str) -> str:
    if kind == "config":
        base = GLib.get_user_config_dir()
    elif kind == "data":
        base = GLib.get_user_data_dir()
    else:
        base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    root = os.path.join(base, app_slug)
    return os.path.join(root, *parts)


def _runtime_root(app_slug: str) -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    candidate = os.path.join(base, app_slug)
    try:
        os.makedirs(candidate, exist_ok=True)
        probe = os.path.join(candidate, ".probe")
        Path(probe).write_text("", encoding="utf-8")
        os.remove(probe)
        return candidate
    except Exception:
        return tempfile.mkdtemp(prefix=f"{app_slug}-")


def dir_uri(path: str) -> str:
    """Return a file:// URI for a directory, ensuring a trailing slash."""
    abspath = os.path.abspath(path)
    if os.path.isdir(abspath) and not abspath.endswith(os.sep):
        abspath = abspath + os.sep
    return "file://" + abspath


def list_local_scripts(folder: str) -> list[str]:
    try:
        names = os.listdir(folder)
    except Exception:
        return []
    scripts: list[str] = []
    for name in names:
        if name.startswith(".") or name in {"__pycache__"} or name.endswith((".pyc", ".pyo")):
            continue
        if name.lower().endswith((".sh", ".py")):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                scripts.append(path)
    return sorted(scripts, key=lambda path: os.path.basename(path).lower())


@dataclass
class AppContext:
    app_id: str = APP_ID
    app_name: str = APP_NAME
    app_slug: str = APP_SLUG
    app_version: str = APP_VERSION
    share_root: str = field(init=False)
    system_regex_dir: str = field(init=False)
    system_post_dir: str = field(init=False)
    system_input_seed_dir: str = field(init=False)
    input_folder: str = field(init=False)
    user_regex_dir: str = field(init=False)
    user_post_dir: str = field(init=False)
    downloads_base: str = field(init=False)
    completed_host: str = field(init=False)
    host_view_text: str = field(init=False)
    shm_root: str = field(init=False)
    shm_text_dir: str = field(init=False)
    shm_toc_dir: str = field(init=False)
    toc_file_path: str = field(init=False)
    combined_pdf_path: str = field(init=False)
    completed_record_pdf: str = field(init=False)

    def __post_init__(self) -> None:
        self.share_root = _share_root(self.app_id)
        self.system_regex_dir = os.path.join(self.share_root, "regexes")
        self.system_post_dir = os.path.join(self.share_root, "post_processing")
        self.system_input_seed_dir = os.path.join(self.share_root, "input_seed")

        self.input_folder = _xdg(self.app_slug, "data", "input")
        self.user_regex_dir = _xdg(self.app_slug, "config", "regexes")
        self.user_post_dir = _xdg(self.app_slug, "config", "post_processing")

        downloads_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        self.downloads_base = downloads_dir or os.path.expanduser("~/Downloads")
        self.completed_host = os.path.join(self.downloads_base, COMPLETED_DIRNAME)
        self.host_view_text = os.path.join(self.downloads_base, TEXTPAGES_DIRNAME)

        self.shm_root = _runtime_root(self.app_slug)
        self.shm_text_dir = os.path.join(self.shm_root, "TextPages")
        self.shm_toc_dir = os.path.join(self.shm_root, "TOC")
        self.toc_file_path = os.path.join(self.shm_toc_dir, "toc.txt")
        self.combined_pdf_path = os.path.join(self.shm_root, "combined_tmp.pdf")
        self.completed_record_pdf = os.path.join(self.completed_host, "bookmarked.pdf")

    # ── Seeding & directory helpers ────────────────────────────────────────
    def seed_user_data(self) -> tuple[bool, bool, bool]:
        try:
            seeded_regex = self._seed_once(self.system_regex_dir, self.user_regex_dir)
            seeded_posts = self._seed_once(self.system_post_dir, self.user_post_dir)
            seeded_input = self._seed_input_once()
            dlog(f"Seeding: regex={seeded_regex} posts={seeded_posts} input={seeded_input}")
            return seeded_regex, seeded_posts, seeded_input
        except Exception as exc:
            dlog(f"Seeding failed: {exc}")
            return False, False, False

    def ensure_runtime_dirs(self) -> None:
        try:
            for folder in (
                self.shm_root,
                self.shm_text_dir,
                self.shm_toc_dir,
                self.input_folder,
                self.user_regex_dir,
                self.user_post_dir,
                self.completed_host,
                self.host_view_text,
            ):
                os.makedirs(folder, exist_ok=True)
            dlog("Created runtime/config/data dirs")
        except Exception as exc:
            dlog(f"Dir create failed: {exc}")

    def reset_toc_file(self) -> None:
        Path(self.shm_toc_dir).mkdir(parents=True, exist_ok=True)
        Path(self.toc_file_path).write_text("", encoding="utf-8")

    def read_toc_text(self) -> str:
        try:
            return Path(self.toc_file_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception as exc:
            return f"__ERROR__:{exc}"

    def list_post_scripts(self) -> list[str]:
        return list_local_scripts(self.user_post_dir)

    # ── Internal helpers ───────────────────────────────────────────────────
    def _seed_once(self, src: str, dst: str) -> bool:
        try:
            if src and os.path.isdir(src) and not os.path.isdir(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copytree(src, dst)
                return True
        except Exception:
            pass
        return False

    def _seed_input_once(self) -> bool:
        try:
            os.makedirs(self.input_folder, exist_ok=True)
            if any(os.scandir(self.input_folder)):
                return False
            if os.path.isdir(self.system_input_seed_dir):
                for name in os.listdir(self.system_input_seed_dir):
                    src = os.path.join(self.system_input_seed_dir, name)
                    if os.path.isfile(src):
                        shutil.copy2(src, os.path.join(self.input_folder, name))
                return True
        except Exception:
            pass
        return False
