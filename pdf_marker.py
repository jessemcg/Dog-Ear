#!/usr/bin/env python3
# Dog Ear – Flatpak (with robust startup + debug logging)

from __future__ import annotations

# ── Standard library ──────────────────────────────────────────────────────────
import os
import sys
import shutil
import subprocess
import threading
import tempfile
from pathlib import Path

# ── Debug helpers ─────────────────────────────────────────────────────────────
DEBUG = os.getenv("DOGEAR_DEBUG") == "1"

def dlog(msg: str) -> None:
    """Emit a debug line to stderr and /tmp/dogear.log when DOGEAR_DEBUG=1."""
    if not DEBUG:
        return
    try:
        line = f"[dogear] {msg}\n"
        sys.stderr.write(line)
        sys.stderr.flush()
        with open("/tmp/dogear.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Logging must never crash the app
        pass

# Default to a GPU backend if the user hasn’t specified one
# (you can override at runtime: GSK_RENDERER=ngl|opengl|cairo)
if "GSK_RENDERER" not in os.environ:
    os.environ["GSK_RENDERER"] = "opengl"

# ── Third party ───────────────────────────────────────────────────────────────
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gdk, Gtk

# ── Local imports ─────────────────────────────────────────────────────────────
APP_DIR = os.path.abspath(os.path.dirname(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from pdfoutline_mod import pdfoutline
from copy_by_number import CopyByNumber
import toc_creator
import about_window

# ── App metadata ──────────────────────────────────────────────────────────────
APP_ID        = "io.github.jessemcgowan.DogEar"
APP_NAME      = "Dog Ear"
APP_SLUG      = "DogEar"
APP_VERSION   = "0.1.0"

# ── Path helpers ──────────────────────────────────────────────────────────────
def _share_root() -> str:
    return f"/app/share/{APP_ID}"

def _xdg(kind: str, *parts: str) -> str:
    if kind == "config":
        base = GLib.get_user_config_dir()
    elif kind == "data":
        base = GLib.get_user_data_dir()
    else:
        base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    root = os.path.join(base, APP_SLUG)
    return os.path.join(root, *parts)

def _runtime_root(appname: str = APP_SLUG) -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    cand = os.path.join(base, appname)
    try:
        os.makedirs(cand, exist_ok=True)
        probe = os.path.join(cand, ".probe")
        Path(probe).write_text("", encoding="utf-8"); os.remove(probe)
        return cand
    except Exception:
        return tempfile.mkdtemp(prefix=f"{appname}-")

def _dir_uri(path: str) -> str:
    """Return a file:// URI for a directory, ensuring trailing slash."""
    ap = os.path.abspath(path)
    if os.path.isdir(ap) and not ap.endswith(os.sep):
        ap = ap + os.sep
    return "file://" + ap

def _list_local_scripts(folder: str) -> list[str]:
    try:
        names = os.listdir(folder)
    except Exception:
        return []
    scripts: list[str] = []
    for n in names:
        if n.startswith(".") or n in {"__pycache__"} or n.endswith((".pyc", ".pyo")):
            continue
        if n.lower().endswith((".sh", ".py")):
            p = os.path.join(folder, n)
            if os.path.isfile(p):
                scripts.append(p)
    return sorted(scripts, key=lambda s: os.path.basename(s).lower())

# ── Persistent locations ──────────────────────────────────────────────────────
SHARE_ROOT             = _share_root()
SYSTEM_REGEX_DIR       = os.path.join(SHARE_ROOT, "regexes")
SYSTEM_POST_DIR        = os.path.join(SHARE_ROOT, "post_processing")
SYSTEM_INPUT_SEED_DIR  = os.path.join(SHARE_ROOT, "input_seed")

input_folder    = _xdg("data",   "input")
USER_REGEX_DIR  = _xdg("config", "regexes")
USER_POST_DIR   = _xdg("config", "post_processing")

downloads_dir = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)

# For creating mirror from in-memory files to Downloads directory
COMPLETED_DIRNAME = f"{APP_SLUG}_Completed"
TEXTPAGES_DIRNAME = f"{APP_SLUG}_TextPages"

completed_host = os.path.join(downloads_dir or os.path.expanduser("~/Downloads"), COMPLETED_DIRNAME)
host_view_text = os.path.join(downloads_dir or os.path.expanduser("~/Downloads"), TEXTPAGES_DIRNAME)

os.makedirs(host_view_text, exist_ok=True)
os.makedirs(completed_host, exist_ok=True)

shm_root            = _runtime_root(APP_SLUG)
shm_text_dir        = os.path.join(shm_root, "TextPages")
shm_toc_dir         = os.path.join(shm_root, "TOC")
toc_file_path       = os.path.join(shm_toc_dir, "toc.txt")
combined_pdf_path   = os.path.join(shm_root, "combined_tmp.pdf")
completed_record_pdf= os.path.join(completed_host, "bookmarked.pdf")

# ── Seed helpers ──────────────────────────────────────────────────────────────
def _seed_once(src: str, dst: str) -> bool:
    try:
        if src and os.path.isdir(src) and not os.path.isdir(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copytree(src, dst)
            return True
    except Exception:
        pass
    return False

def _seed_input_once() -> bool:
    try:
        os.makedirs(input_folder, exist_ok=True)
        if any(os.scandir(input_folder)):
            return False
        if os.path.isdir(SYSTEM_INPUT_SEED_DIR):
            for name in os.listdir(SYSTEM_INPUT_SEED_DIR):
                s = os.path.join(SYSTEM_INPUT_SEED_DIR, name)
                if os.path.isfile(s):
                    shutil.copy2(s, os.path.join(input_folder, name))
            return True
    except Exception:
        pass
    return False

# ── Main window ───────────────────────────────────────────────────────────────
class DogEarWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        dlog("DogEarWindow.__init__()")
        super().__init__(application=app, title=APP_NAME)
        self.add_css_class("rounded-window")
        self.set_default_size(980, 640)

        # Load CSS (non-fatal if missing)
        try:
            css_path = os.path.join(_share_root(), "style.css")
            provider = Gtk.CssProvider()
            provider.load_from_path(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
            )
            dlog(f"Loaded CSS: {css_path}")
        except Exception as e:
            dlog(f"CSS load failed: {e}")

        # First-run seeding (non-fatal)
        try:
            seeded_regex = _seed_once(SYSTEM_REGEX_DIR, USER_REGEX_DIR)
            seeded_posts = _seed_once(SYSTEM_POST_DIR,  USER_POST_DIR)
            seeded_input = _seed_input_once()
            dlog(f"Seeding: regex={seeded_regex} posts={seeded_posts} input={seeded_input}")
        except Exception as e:
            dlog(f"Seeding failed: {e}")
            seeded_regex = seeded_posts = seeded_input = False

        # Ensure required dirs exist
        try:
            for d in (shm_root, shm_text_dir, shm_toc_dir, input_folder,
                      USER_REGEX_DIR, USER_POST_DIR, completed_host, host_view_text):
                os.makedirs(d, exist_ok=True)
            dlog("Created runtime/config/data dirs")
        except Exception as e:
            dlog(f"Dir create failed: {e}")

        # Tell CopyByNumber to use the in-memory TextPages directory
        self.text_record_folder = shm_text_dir
        os.environ["PDFMARKER_TEXT_PAGES_DIR"] = shm_text_dir

        self._reset_toc_file()
        if not os.path.exists(toc_file_path):
            Path(toc_file_path).write_text("", encoding="utf-8")

        self._saving_debounce_id: int | None = None
        self._is_writing = False
        self._last_disk_text = ""

        # ── UI layout ──────────────────────────────────────────────────────────
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        hb = Adw.HeaderBar()

        title = Gtk.Label(label="Dog Ear")
        title.add_css_class("bold-title")
        hb.set_title_widget(title)

        # "+" button on the left
        self.plus_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self.plus_btn.set_tooltip_text("Add")
        self.plus_btn.remove_css_class("suggested-action")
        self.plus_btn.add_css_class("flat")
        self.plus_btn.connect("clicked", self._on_plus_clicked)
        hb.pack_start(self.plus_btn)

        # Hamburger menu on the right
        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self._ensure_app_menu_actions()
        self.menu_btn.set_popover(self._build_app_menu())
        hb.pack_end(self.menu_btn)

        # Place headerbar in main window
        toolbar_view.add_top_bar(hb)

        # Main content under headerbar
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        toolbar_view.set_content(card)

        # Adw. boxed list for Create TOC / Bookmarks / Open Completed Directory
        group = Adw.PreferencesGroup()
        group.add_css_class("list-stack")
        group.set_hexpand(True)
        card.append(group)

        # Create TOC row
        self.row_toc = Adw.ActionRow(title="Create TOC")
        self.row_toc.add_css_class("list-card")
        self.row_toc.set_activatable(True)
        self.row_toc.add_prefix(Gtk.Image.new_from_icon_name("view-list-ordered-symbolic"))
        self.lbl_toc_status = Gtk.Label(label="Idle")
        self.spinner_toc = Gtk.Spinner(spinning=False)
        self.row_toc.add_suffix(self.lbl_toc_status)
        self.row_toc.add_suffix(self.spinner_toc)
        group.add(self.row_toc)

        # Create Bookmarks row
        self.row_bm = Adw.ActionRow(title="Create Bookmarks")
        self.row_bm.add_css_class("list-card")
        self.row_bm.set_activatable(True)
        self.row_bm.add_prefix(Gtk.Image.new_from_icon_name("bookmark-new-symbolic"))
        self.lbl_bm_status = Gtk.Label(label="Idle")
        self.spinner_bm = Gtk.Spinner(spinning=False)
        self.row_bm.add_suffix(self.lbl_bm_status)
        self.row_bm.add_suffix(self.spinner_bm)
        group.add(self.row_bm)

        # Open Completed Directory row
        self.row_dir = Adw.ActionRow(title="Open Completed Directory")
        self.row_dir.add_css_class("list-card")
        self.row_dir.set_activatable(True)
        self.row_dir.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        group.add(self.row_dir)

        # Row activations
        self.row_toc.connect("activated", self._on_row_create_toc)
        self.row_bm.connect("activated", self._on_row_create_bookmarks)
        self.row_dir.connect("activated", lambda *_: self._open_path(None, completed_host, False))

        # Text Editor Section
        editor_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        editor_section.set_vexpand(True)
        editor_section.set_hexpand(True)
        card.append(editor_section)

        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        editor_section.append(hdr)
        hdr.append(Gtk.Label(label="TOC (editable):", xalign=0))

        self.scripts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hdr.append(self.scripts_box)
        self._rebuild_script_buttons()

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add_css_class("editor-frame")
        scroller.set_hexpand(True); scroller.set_vexpand(True)
        editor_section.append(scroller)

        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.NONE)
        self.textview.set_monospace(True)
        self.textview.set_pixels_above_lines(2)
        self.textview.set_pixels_below_lines(2)
        self.textview.add_css_class("editor-textview")
        self.textbuffer = self.textview.get_buffer()
        self.textbuffer.connect("changed", self._on_buffer_changed)
        scroller.set_child(self.textview)

        # Footer status
        self.status = Gtk.Label(label="", xalign=0)
        self.status.add_css_class("footer-status")
        self.status.set_wrap(True)
        self.status.set_hexpand(True)
        self.status.set_halign(Gtk.Align.FILL)
        card.append(self.status)
        self._width_group = Gtk.SizeGroup.new(Gtk.SizeGroupMode.HORIZONTAL)
        self._width_group.add_widget(scroller)
        self._width_group.add_widget(self.status)

        # First-run notice
        msgs = []
        try:
            if seeded_input: msgs.append("a sample PDF to Input")
            if seeded_regex: msgs.append("default regexes to REGEX")
            if seeded_posts: msgs.append("default post scripts to Post Scripts")
        except Exception:
            pass
        if msgs:
            self._set_status("First run: added " + " and ".join(msgs) + ".")

        # Load TOC + monitor
        self._load_toc_from_disk()
        self._toc_gfile = Gio.File.new_for_path(toc_file_path)
        try:
            self._toc_monitor = self._toc_gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._toc_monitor.connect("changed", self._on_toc_file_changed)
            dlog("File monitor armed")
        except Exception as e:
            dlog(f"File monitor failed: {e}")
            self._toc_monitor = None

        dlog("DogEarWindow constructed")

    # ── Menu + actions ────────────────────────────────────────────────────────
    def _ensure_app_menu_actions(self):
        app = self.get_application()

        def ensure(name: str, handler):
            if not app.lookup_action(name):
                act = Gio.SimpleAction.new(name, None)
                act.connect("activate", handler)
                app.add_action(act)

        ensure("open_input",        lambda *_: self._open_path(None, input_folder, False))
        ensure("open_regex",        lambda *_: self._open_path(None, USER_REGEX_DIR, False))
        ensure("open_posts",        lambda *_: self._open_path(None, USER_POST_DIR, False))
        ensure("open_text",         lambda *_: self._open_path(None, shm_text_dir, True))
        ensure("copy_text_number",  self._on_copy_text_number)
        ensure("copy_regex_pattern", self._on_copy_regex_pattern)
        ensure("open_completed",    lambda *_: self._open_path(None, completed_host, False))
        ensure("about",             self._on_about)

    def _build_app_menu(self) -> Gtk.PopoverMenu:
        menu = Gio.Menu()
        sec_open = Gio.Menu()
        sec_open.append("Input",                    "app.open_input")
        sec_open.append("Regular Expressions",      "app.open_regex")
        sec_open.append("TOC Scripts",              "app.open_posts")
        sec_open.append("Text Files",               "app.open_text")
        menu.append_section(None, sec_open)

        sec_copy = Gio.Menu()
        sec_copy.append("Copy Page Text", "app.copy_text_number")
        sec_copy.append("Copy Regex Template", "app.copy_regex_pattern")
        menu.append_section(None, sec_copy)

        sec_about = Gio.Menu()
        sec_about.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, sec_about)

        return Gtk.PopoverMenu.new_from_model(menu)

    def _ensure_plus_menu_actions(self):
        app = self.get_application()

        def ensure(name: str, handler):
            if not app.lookup_action(name):
                act = Gio.SimpleAction.new(name, None)
                act.connect("activate", handler)
                app.add_action(act)

        ensure("add_pdfs",    lambda *_: self._on_add_pdfs(None))
        ensure("clear_input", lambda *_: self._on_clear_input(None))

        app.set_accels_for_action("app.add_pdfs",    ["<Primary>N"])
        app.set_accels_for_action("app.clear_input", ["<Primary><Shift>D"])

    def _build_plus_menu(self) -> Gtk.PopoverMenu:
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Add PDFs",   "app.add_pdfs")
        sec.append("Clear Input", "app.clear_input")
        menu.append_section(None, sec)
        return Gtk.PopoverMenu.new_from_model(menu)

    def _on_plus_clicked(self, button):
        if not hasattr(self, "plus_menu"):
            self._ensure_plus_menu_actions()
            self.plus_menu = self._build_plus_menu()
            self.plus_menu.set_parent(button)
            self.plus_menu.set_has_arrow(True)
            self.plus_menu.set_autohide(True)
        self.plus_menu.popup()
        
    # ── Plus-menu handlers (Add PDFs / Clear Input) ───────────────────────────
    def _on_clear_input(self, *_):
        """Clear the Input folder and reset transient state (combined pdf, text pages, TOC)."""
        def work():
            try:
                # 1) Clear Input dir
                Path(input_folder).mkdir(parents=True, exist_ok=True)
                for entry in list(Path(input_folder).iterdir()):
                    try:
                        entry.unlink() if entry.is_file() or entry.is_symlink() else shutil.rmtree(entry)
                    except Exception:
                        pass

                # 2) Clear in-memory text pages
                try:
                    for entry in list(Path(shm_text_dir).iterdir()):
                        try:
                            entry.unlink() if entry.is_file() or entry.is_symlink() else shutil.rmtree(entry)
                        except Exception:
                            pass
                except Exception:
                    pass

                # 3) Remove any stale combined.pdf
                try:
                    if os.path.exists(combined_pdf_path):
                        os.remove(combined_pdf_path)
                except Exception:
                    pass

                # 4) Reset TOC file + editor
                self._reset_toc_file()
                self._load_toc_from_disk()

                self._set_status("Input cleared. TOC & text pages reset.")
            except Exception as e:
                self._set_status(f"Clear Input failed: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _on_add_pdfs(self, *_):
        """Pick one or more PDFs and copy them into the Input folder (overwriting same-named files)."""
        dialog = Gtk.FileDialog()

        # Optional but nice: restrict to PDFs
        flt = Gtk.FileFilter()
        flt.set_name("PDF files")
        flt.add_suffix("pdf")
        flt.add_mime_type("application/pdf")
        dialog.set_default_filter(flt)

        def finished(dlg, result):
            try:
                files = dlg.open_multiple_finish(result)
            except Exception as e:
                # Canceled or failed
                self._set_status(f"Add PDFs canceled or failed: {e}")
                return

            # Copy on a thread so UI stays responsive
            def copy_work():
                try:
                    Path(input_folder).mkdir(parents=True, exist_ok=True)
                    count = 0
                    for gfile in files:
                        try:
                            path = gfile.get_path()
                            if not path:
                                continue
                            # Only accept .pdf (case-insensitive)
                            if not path.lower().endswith(".pdf"):
                                continue
                            dst = os.path.join(input_folder, os.path.basename(path))
                            shutil.copy2(path, dst)
                            count += 1
                        except Exception:
                            pass

                    if count == 0:
                        self._set_status("No PDFs added.")
                    else:
                        self._set_status(f"Added {count} PDF(s) to Input.")
                except Exception as e:
                    self._set_status(f"Add PDFs failed: {e}")

            threading.Thread(target=copy_work, daemon=True).start()

        dialog.open_multiple(self, None, finished)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _mirror_tree(self, src: str, dst: str):
        try:
            os.makedirs(dst, exist_ok=True)
            for name in os.listdir(dst):
                p = os.path.join(dst, name)
                try:
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                except Exception:
                    pass
            for name in os.listdir(src):
                s = os.path.join(src, name)
                d = os.path.join(dst, name)
                try:
                    shutil.copytree(s, d) if os.path.isdir(s) else shutil.copy2(s, d)
                except Exception:
                    pass
        except Exception as e:
            self._set_status(f"Mirror failed: {e}")

    def _open_path(self, _btn, path: str, mirror: bool):
        try:
            os.makedirs(path, exist_ok=True)
            open_path = path
            if mirror and os.path.abspath(path) == os.path.abspath(shm_text_dir):
                self._mirror_tree(path, host_view_text)
                open_path = host_view_text

            uri = _dir_uri(open_path)

            if hasattr(Gtk, "UriLauncher"):
                launcher = Gtk.UriLauncher.new(uri)
                def done(l, res):
                    try:
                        ok = l.launch_finish(res)
                        if not ok:
                            try:
                                Gio.AppInfo.launch_default_for_uri(uri, None); return
                            except Exception:
                                pass
                            subprocess.run(["xdg-open", uri], check=True)
                    except Exception:
                        try:
                            Gio.AppInfo.launch_default_for_uri(uri, None); return
                        except Exception:
                            pass
                        try:
                            subprocess.run(["xdg-open", uri], check=True)
                        except Exception as e2:
                            self._set_status(f"Open failed: {open_path} — {e2}")
                launcher.launch(self, None, done)
                return

            try:
                Gio.AppInfo.launch_default_for_uri(uri, None); return
            except Exception:
                pass

            try:
                subprocess.run(["xdg-open", uri], check=True); return
            except Exception as e:
                self._set_status(f"Open failed: {open_path} — {e}")

        except Exception as e:
            self._set_status(f"Open failed: {path} — {e}")

    def _on_copy_text_number(self, *_):
        CopyByNumber.on_copy_text_number(self)

    def _on_copy_regex_pattern(self, *_):
        pattern = r"\A(?=[\s\S]*HELPER_PATTERN)[\s\S]*?(TARGET_PATTERN)"
        CopyByNumber._copy_text_async(self, pattern, verify=False)

    def _set_status(self, text: str):
        dlog(f"STATUS: {text}")
        GLib.idle_add(self.status.set_text, text)

    def _begin_action(self, which: str):
        lbl = self.lbl_toc_status if which == "toc" else self.lbl_bm_status
        spinner = self.spinner_toc if which == "toc" else self.spinner_bm
        lbl.set_text("")
        spinner.start()
        self.row_toc.set_sensitive(False)
        self.row_bm.set_sensitive(False)

    def _finish_action(self, which: str, status: str):
        def _update():
            lbl = self.lbl_toc_status if which == "toc" else self.lbl_bm_status
            spinner = self.spinner_toc if which == "toc" else self.spinner_bm
            lbl.set_text(status)
            spinner.stop()
            self.row_toc.set_sensitive(True)
            self.row_bm.set_sensitive(True)
            return False

        GLib.idle_add(_update)

    def _reset_toc_file(self):
        Path(shm_toc_dir).mkdir(parents=True, exist_ok=True)
        Path(toc_file_path).write_text("", encoding="utf-8")

    def _read_disk_text(self) -> str:
        try:
            return Path(toc_file_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception as e:
            self._set_status(f"Read failed: {e}"); return ""

    def _load_toc_from_disk(self, *_):
        text = self._read_disk_text()
        self._last_disk_text = text
        GLib.idle_add(self.textbuffer.set_text, text)
        self._set_status("Loaded TOC.")

    def _buffer_text(self) -> str:
        it0, it1 = self.textbuffer.get_start_iter(), self.textbuffer.get_end_iter()
        return self.textbuffer.get_text(it0, it1, False)

    def _write_buffer_to_disk(self):
        self._saving_debounce_id = None
        text = self._buffer_text()
        if text == self._last_disk_text:
            return False
        try:
            self._is_writing = True
            Path(shm_toc_dir).mkdir(parents=True, exist_ok=True)
            Path(toc_file_path).write_text(text, encoding="utf-8")
            self._last_disk_text = text
            self._set_status("Saved TOC.")
        except Exception as e:
            self._set_status(f"Save failed: {e}")
        finally:
            GLib.timeout_add(150, self._clear_is_writing)
        return False

    def _write_buffer_to_disk_immediate(self):
        if self._saving_debounce_id is not None:
            GLib.source_remove(self._saving_debounce_id)
            self._saving_debounce_id = None
        self._write_buffer_to_disk()

    def _clear_is_writing(self):
        self._is_writing = False
        return False

    def _on_buffer_changed(self, *_):
        if self._saving_debounce_id is not None:
            GLib.source_remove(self._saving_debounce_id)
        self._saving_debounce_id = GLib.timeout_add(300, self._write_buffer_to_disk)

    def _on_toc_file_changed(self, *_args):
        if self._is_writing: return
        self._load_toc_from_disk()

    # ── Long-running actions ──────────────────────────────────────────────────
    def _on_row_create_toc(self, *_):
        self._begin_action("toc")
        threading.Thread(target=self._run_create_toc, daemon=True).start()

    def _run_create_toc(self):
        try:
            self._do_create_toc()
            self._finish_action("toc", "Done")
        except Exception as e:
            self._finish_action("toc", "Error")
            self._set_status(f"Create TOC failed: {e}")

    def _do_create_toc(self):
        # Ensure required dirs exist
        for d in (shm_root, shm_text_dir, shm_toc_dir, input_folder):
            Path(d).mkdir(parents=True, exist_ok=True)

        # Remove old combined.pdf
        try:
            if os.path.exists(combined_pdf_path):
                os.remove(combined_pdf_path)
        except Exception:
            pass

        # Wipe stale per-page text files
        try:
            text_dir = Path(shm_text_dir)
            if text_dir.exists():
                for item in list(text_dir.iterdir()):
                    try:
                        if item.is_file() or item.is_symlink():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item)
                    except Exception:
                        pass
        except Exception:
            pass

        # Perform TOC creation + fresh per-page extraction
        toc_creator.create_toc(
            text_record_folder=shm_text_dir,
            input_folder=input_folder,
            combined_pdf_path=combined_pdf_path,
            toc_file=toc_file_path,
            regexes_folder=USER_REGEX_DIR,
            update_progress=lambda f: None,
        )

        # Reload UI from disk once finished
        self._load_toc_from_disk()

    def _on_row_create_bookmarks(self, *_):
        self._begin_action("bm")
        threading.Thread(target=self._run_create_bookmarks, daemon=True).start()

    def _run_create_bookmarks(self):
        try:
            self._do_create_bookmarks()
            self._finish_action("bm", "Done")
        except Exception as e:
            self._finish_action("bm", "Error")
            self._set_status(f"Create Bookmarks failed: {e}")

    def _do_create_bookmarks(self):
        self._write_buffer_to_disk_immediate()
        pdfoutline(
            inpdf=combined_pdf_path,
            tocfile=toc_file_path,
            outpdf=completed_record_pdf,
            update_progress=lambda f: None,
        )

    # ── File operations ───────────────────────────────────────────────────────
    def _rebuild_script_buttons(self):
        # clear existing
        child = self.scripts_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.scripts_box.remove(child)
            child = nxt

        scripts = _list_local_scripts(USER_POST_DIR)
        if not scripts:
            hint = Gtk.Label(label="(Place .sh or .py in Post Scripts)", xalign=0)
            self.scripts_box.append(hint)
            return

        for spath in scripts:
            base = os.path.basename(spath)
            label, _ = os.path.splitext(base)
            btn = Gtk.Button(label=label)
            btn.add_css_class("script-chip")
            btn.set_tooltip_text(f"Run {base} in the TOC folder")
            btn.connect("clicked", self._on_run_script_clicked, spath)
            self.scripts_box.append(btn)

    def _on_run_script_clicked(self, _btn, script_path: str):
        threading.Thread(target=self._run_script_in_toc_dir, args=(script_path,), daemon=True).start()

    def _run_script_in_toc_dir(self, script_path: str):
        try:
            self._write_buffer_to_disk_immediate()

            ext = os.path.splitext(script_path)[1].lower()
            if ext == ".sh":
                cmd = ["bash", script_path]
            elif ext == ".py":
                cmd = [sys.executable, script_path]
            else:
                cmd = [script_path]

            env = dict(os.environ)
            env["DOGEAR_TOC"] = toc_file_path
            env["DOGEAR_TEXTDIR"] = shm_text_dir

            proc = subprocess.run(cmd, cwd=shm_toc_dir, capture_output=True, text=True, env=env)
            if proc.returncode != 0:
                raise RuntimeError(f"exit {proc.returncode}. stderr:\n{proc.stderr or '(none)'}")

            self._load_toc_from_disk()
            self._mirror_tree(shm_text_dir, host_view_text)

            out = (proc.stdout or "").strip()
            self._set_status(f"Script '{os.path.basename(script_path)}' completed." + (f" Output: {out}" if out else ""))
        except Exception as e:
            self._set_status(f"Script failed: {e}")

    def _on_about(self, *_):
        about_window.show_about_default(
            self,
            app_id=APP_ID,
            app_name=APP_NAME,
            version=APP_VERSION,
        )

# ── Application ───────────────────────────────────────────────────────────────
class DogEarApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win = None  # keep a reference

    def do_startup(self, *_):
        dlog("do_startup()")
        Adw.Application.do_startup(self)
        try:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        except Exception as e:
            dlog(f"StyleManager failed: {e}")

    def do_activate(self, *_):
        dlog("do_activate()")
        if not self.win:
            try:
                self.win = DogEarWindow(self)  # keep it alive
                dlog("Main window constructed")
            except Exception as e:
                dlog(f"Window init failed: {e}")
                # Present a minimal emergency window so activation is visible
                self.win = Adw.ApplicationWindow(application=self, title="Dog Ear (startup error)")
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              margin_top=24, margin_bottom=24, margin_start=24, margin_end=24, spacing=12)
                lab = Gtk.Label(label=f"Startup error:\n{e}", xalign=0)
                box.append(lab)
                self.win.set_content(box)
        self.win.present()
        dlog("present() called")

# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if DEBUG:
        try:
            import faulthandler, signal  # type: ignore
            faulthandler.enable()
            faulthandler.register(signal.SIGUSR2)
        except Exception:
            pass
        dlog("Starting DogEarApp()")
    try:
        DogEarApp().run(None)
    except Exception as e:
        dlog(f"FATAL: {e}")
        raise

