from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gdk, Gtk

from app_context import (
    APP_ID,
    APP_NAME,
    APP_VERSION,
    AppContext,
    dlog,
    dir_uri,
)
from copy_by_number import CopyByNumber
import about_window
from workflows import WorkflowRunner


class DogEarWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        dlog("DogEarWindow.__init__()")
        super().__init__(application=app, title=APP_NAME)
        self.add_css_class("rounded-window")
        self.set_default_size(980, 640)

        self.ctx = AppContext()
        self._saving_debounce_id: int | None = None
        self._is_writing = False
        self._last_disk_text = ""

        self.runner = WorkflowRunner(
            self.ctx, self._set_status, self._load_toc_from_disk, self._reset_toc_file
        )

        # Load CSS (non-fatal if missing)
        try:
            css_path = os.path.join(self.ctx.share_root, "style.css")
            provider = Gtk.CssProvider()
            provider.load_from_path(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_USER
            )
            dlog(f"Loaded CSS: {css_path}")
        except Exception as exc:
            dlog(f"CSS load failed: {exc}")

        seeded_regex, seeded_posts, seeded_input = self.ctx.seed_user_data()
        self.ctx.ensure_runtime_dirs()

        os.environ["PDFMARKER_TEXT_PAGES_DIR"] = self.ctx.shm_text_dir

        self._reset_toc_file()
        if not os.path.exists(self.ctx.toc_file_path):
            Path(self.ctx.toc_file_path).write_text("", encoding="utf-8")

        # ── UI layout ──────────────────────────────────────────────────────
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar()

        title = Gtk.Label(label=APP_NAME)
        title.add_css_class("bold-title")
        header.set_title_widget(title)

        self.plus_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self.plus_btn.set_tooltip_text("Add")
        self.plus_btn.remove_css_class("suggested-action")
        self.plus_btn.add_css_class("flat")
        self.plus_btn.connect("clicked", self._on_plus_clicked)
        header.pack_start(self.plus_btn)

        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self._ensure_app_menu_actions()
        self.menu_btn.set_popover(self._build_app_menu())
        header.pack_end(self.menu_btn)

        toolbar_view.add_top_bar(header)

        card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        toolbar_view.set_content(card)

        group = Adw.PreferencesGroup()
        group.add_css_class("list-stack")
        group.set_hexpand(True)
        card.append(group)

        self.row_toc = Adw.ActionRow(title="Create TOC")
        self.row_toc.add_css_class("list-card")
        self.row_toc.set_activatable(True)
        self.row_toc.add_prefix(Gtk.Image.new_from_icon_name("view-list-ordered-symbolic"))
        self.lbl_toc_status = Gtk.Label(label="Idle")
        self.spinner_toc = Gtk.Spinner(spinning=False)
        self.row_toc.add_suffix(self.lbl_toc_status)
        self.row_toc.add_suffix(self.spinner_toc)
        group.add(self.row_toc)

        self.row_bm = Adw.ActionRow(title="Create Bookmarks")
        self.row_bm.add_css_class("list-card")
        self.row_bm.set_activatable(True)
        self.row_bm.add_prefix(Gtk.Image.new_from_icon_name("bookmark-new-symbolic"))
        self.lbl_bm_status = Gtk.Label(label="Idle")
        self.spinner_bm = Gtk.Spinner(spinning=False)
        self.row_bm.add_suffix(self.lbl_bm_status)
        self.row_bm.add_suffix(self.spinner_bm)
        group.add(self.row_bm)

        self.row_dir = Adw.ActionRow(title="Open Completed Directory")
        self.row_dir.add_css_class("list-card")
        self.row_dir.set_activatable(True)
        self.row_dir.add_prefix(Gtk.Image.new_from_icon_name("folder-open-symbolic"))
        group.add(self.row_dir)

        self.row_toc.connect("activated", self._on_row_create_toc)
        self.row_bm.connect("activated", self._on_row_create_bookmarks)
        self.row_dir.connect("activated", lambda *_: self._open_path(None, self.ctx.completed_host))

        editor_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        editor_section.set_vexpand(True)
        editor_section.set_hexpand(True)
        card.append(editor_section)

        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        editor_section.append(header_row)
        header_row.append(Gtk.Label(label="TOC (editable):", xalign=0))

        self.scripts_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_row.append(self.scripts_box)
        self._rebuild_script_buttons()

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add_css_class("editor-frame")
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
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

        self.status = Gtk.Label(label="", xalign=0)
        self.status.add_css_class("footer-status")
        self.status.set_wrap(True)
        self.status.set_hexpand(True)
        self.status.set_halign(Gtk.Align.FILL)
        card.append(self.status)

        self._width_group = Gtk.SizeGroup.new(Gtk.SizeGroupMode.HORIZONTAL)
        self._width_group.add_widget(scroller)
        self._width_group.add_widget(self.status)

        msgs = []
        try:
            if seeded_input:
                msgs.append("a sample PDF to Input")
            if seeded_regex:
                msgs.append("default regexes to REGEX")
            if seeded_posts:
                msgs.append("default post scripts to Post Scripts")
        except Exception:
            pass
        if msgs:
            self._set_status("First run: added " + " and ".join(msgs) + ".")

        self._load_toc_from_disk()
        self._toc_gfile = Gio.File.new_for_path(self.ctx.toc_file_path)
        try:
            self._toc_monitor = self._toc_gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._toc_monitor.connect("changed", self._on_toc_file_changed)
            dlog("File monitor armed")
        except Exception as exc:
            dlog(f"File monitor failed: {exc}")
            self._toc_monitor = None

        dlog("DogEarWindow constructed")

    # ── Menu + actions ─────────────────────────────────────────────────---
    def _ensure_app_menu_actions(self) -> None:
        app = self.get_application()

        def ensure(name: str, handler):
            if not app.lookup_action(name):
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", handler)
                app.add_action(action)

        ensure("open_input", lambda *_: self._open_path(None, self.ctx.input_folder))
        ensure("open_regex", lambda *_: self._open_path(None, self.ctx.user_regex_dir))
        ensure("open_posts", lambda *_: self._open_path(None, self.ctx.user_post_dir))
        ensure("open_text", lambda *_: self._open_path(None, self.ctx.shm_text_dir))
        ensure("copy_text_number", self._on_copy_text_number)
        ensure("copy_regex_pattern", self._on_copy_regex_pattern)
        ensure("open_completed", lambda *_: self._open_path(None, self.ctx.completed_host))
        ensure("about", self._on_about)

    def _build_app_menu(self) -> Gtk.PopoverMenu:
        menu = Gio.Menu()
        sec_open = Gio.Menu()
        sec_open.append("Input", "app.open_input")
        sec_open.append("Regular Expressions", "app.open_regex")
        sec_open.append("TOC Scripts", "app.open_posts")
        sec_open.append("Text Files", "app.open_text")
        menu.append_section(None, sec_open)

        sec_copy = Gio.Menu()
        sec_copy.append("Copy Page Text", "app.copy_text_number")
        sec_copy.append("Copy Regex Template", "app.copy_regex_pattern")
        menu.append_section(None, sec_copy)

        sec_about = Gio.Menu()
        sec_about.append(f"About {APP_NAME}", "app.about")
        menu.append_section(None, sec_about)

        return Gtk.PopoverMenu.new_from_model(menu)

    def _ensure_plus_menu_actions(self) -> None:
        app = self.get_application()

        def ensure(name: str, handler):
            if not app.lookup_action(name):
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", handler)
                app.add_action(action)

        ensure("add_pdfs", lambda *_: self._on_add_pdfs(None))
        ensure("clear_input", lambda *_: self._on_clear_input(None))

        app.set_accels_for_action("app.add_pdfs", ["<Primary>N"])
        app.set_accels_for_action("app.clear_input", ["<Primary><Shift>D"])

    def _build_plus_menu(self) -> Gtk.PopoverMenu:
        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Add PDFs", "app.add_pdfs")
        sec.append("Clear Input", "app.clear_input")
        menu.append_section(None, sec)
        return Gtk.PopoverMenu.new_from_model(menu)

    def _on_plus_clicked(self, button: Gtk.Button) -> None:
        if not hasattr(self, "plus_menu"):
            self._ensure_plus_menu_actions()
            self.plus_menu = self._build_plus_menu()
            self.plus_menu.set_parent(button)
            self.plus_menu.set_has_arrow(True)
            self.plus_menu.set_autohide(True)
        self.plus_menu.popup()

    # ── Plus-menu handlers ─────────────────────────────────────────────---
    def _on_clear_input(self, *_args) -> None:
        threading.Thread(target=self.runner.clear_input, daemon=True).start()

    def _on_add_pdfs(self, *_args) -> None:
        dialog = Gtk.FileDialog()

        file_filter = Gtk.FileFilter()
        file_filter.set_name("PDF files")
        file_filter.add_suffix("pdf")
        file_filter.add_mime_type("application/pdf")
        dialog.set_default_filter(file_filter)

        def finished(dlg: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                files = dlg.open_multiple_finish(result)
            except Exception as exc:
                self._set_status(f"Add PDFs canceled or failed: {exc}")
                return

            paths = [gfile.get_path() or "" for gfile in files]

            threading.Thread(
                target=self.runner.copy_pdfs_into_input,
                args=(paths,),
                daemon=True,
            ).start()

        dialog.open_multiple(self, None, finished)

    # ── Status helpers ─────────────────────────────────────────────────---
    def _set_status(self, text: str) -> None:
        dlog(f"STATUS: {text}")
        GLib.idle_add(self.status.set_text, text)

    def _begin_action(self, which: str) -> None:
        label = self.lbl_toc_status if which == "toc" else self.lbl_bm_status
        spinner = self.spinner_toc if which == "toc" else self.spinner_bm
        label.set_text("")
        spinner.start()
        self.row_toc.set_sensitive(False)
        self.row_bm.set_sensitive(False)

    def _finish_action(self, which: str, status: str) -> None:
        def update() -> bool:
            label = self.lbl_toc_status if which == "toc" else self.lbl_bm_status
            spinner = self.spinner_toc if which == "toc" else self.spinner_bm
            label.set_text(status)
            spinner.stop()
            self.row_toc.set_sensitive(True)
            self.row_bm.set_sensitive(True)
            return False

        GLib.idle_add(update)

    def _reset_toc_file(self) -> None:
        self.ctx.reset_toc_file()

    def _read_disk_text(self) -> str:
        try:
            return Path(self.ctx.toc_file_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except Exception as exc:
            self._set_status(f"Read failed: {exc}")
            return ""

    def _load_toc_from_disk(self, *_args) -> None:
        text = self._read_disk_text()
        self._last_disk_text = text
        GLib.idle_add(self.textbuffer.set_text, text)
        self._set_status("Loaded TOC.")

    def _buffer_text(self) -> str:
        start, end = self.textbuffer.get_start_iter(), self.textbuffer.get_end_iter()
        return self.textbuffer.get_text(start, end, False)

    def _write_buffer_to_disk(self) -> bool:
        self._saving_debounce_id = None
        text = self._buffer_text()
        if text == self._last_disk_text:
            return False
        try:
            self._is_writing = True
            Path(self.ctx.shm_toc_dir).mkdir(parents=True, exist_ok=True)
            Path(self.ctx.toc_file_path).write_text(text, encoding="utf-8")
            self._last_disk_text = text
            self._set_status("Saved TOC.")
        except Exception as exc:
            self._set_status(f"Save failed: {exc}")
        finally:
            GLib.timeout_add(150, self._clear_is_writing)
        return False

    def _write_buffer_to_disk_immediate(self) -> None:
        if self._saving_debounce_id is not None:
            GLib.source_remove(self._saving_debounce_id)
            self._saving_debounce_id = None
        self._write_buffer_to_disk()

    def _clear_is_writing(self) -> bool:
        self._is_writing = False
        return False

    def _on_buffer_changed(self, *_args) -> None:
        if self._saving_debounce_id is not None:
            GLib.source_remove(self._saving_debounce_id)
        self._saving_debounce_id = GLib.timeout_add(300, self._write_buffer_to_disk)

    def _on_toc_file_changed(self, *_args) -> None:
        if self._is_writing:
            return
        self._load_toc_from_disk()

    # ── Long-running actions ─────────────────────────────────────────────-
    def _on_row_create_toc(self, *_args) -> None:
        self._begin_action("toc")
        threading.Thread(target=self._run_create_toc, daemon=True).start()

    def _run_create_toc(self) -> None:
        try:
            self.runner.create_toc()
            self._finish_action("toc", "Done")
        except Exception as exc:
            self._finish_action("toc", "Error")
            self._set_status(f"Create TOC failed: {exc}")

    def _on_row_create_bookmarks(self, *_args) -> None:
        self._begin_action("bm")
        threading.Thread(target=self._run_create_bookmarks, daemon=True).start()

    def _run_create_bookmarks(self) -> None:
        try:
            self._write_buffer_to_disk_immediate()
            self.runner.create_bookmarks()
            self._finish_action("bm", "Done")
        except Exception as exc:
            self._finish_action("bm", "Error")
            self._set_status(f"Create Bookmarks failed: {exc}")

    # ── File operations ─────────────────────────────────────────────────--
    def _rebuild_script_buttons(self) -> None:
        child = self.scripts_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.scripts_box.remove(child)
            child = nxt

        scripts = self.ctx.list_post_scripts()
        if not scripts:
            hint = Gtk.Label(label="(Place .sh or .py in Post Scripts)", xalign=0)
            self.scripts_box.append(hint)
            return

        for script_path in scripts:
            base = os.path.basename(script_path)
            label, _ext = os.path.splitext(base)
            button = Gtk.Button(label=label)
            button.add_css_class("script-chip")
            button.set_tooltip_text(f"Run {base} in the TOC folder")
            button.connect("clicked", self._on_run_script_clicked, script_path)
            self.scripts_box.append(button)

    def _on_run_script_clicked(self, _button: Gtk.Button, script_path: str) -> None:
        self._write_buffer_to_disk_immediate()
        threading.Thread(
            target=self.runner.run_script_in_toc_dir,
            args=(script_path,),
            daemon=True,
        ).start()

    def _open_path(self, _button, path: str) -> None:
        try:
            os.makedirs(path, exist_ok=True)
            uri = dir_uri(path)

            if hasattr(Gtk, "FileLauncher"):
                file = Gio.File.new_for_path(path)
                launcher = Gtk.FileLauncher.new(file)

                def done(launcher_obj: Gtk.FileLauncher, result: Gio.AsyncResult) -> None:
                    try:
                        ok = launcher_obj.launch_finish(result)
                        if not ok:
                            self._launch_uri(uri, path)
                    except Exception:
                        self._launch_uri(uri, path)

                launcher.launch(self, None, done)
                return

            self._launch_uri(uri, path)
        except Exception as exc:
            self._set_status(f"Open failed: {path} — {exc}")

    def _launch_uri(self, uri: str, path: str) -> None:
        if hasattr(Gtk, "UriLauncher"):
            launcher = Gtk.UriLauncher.new(uri)

            def done(launcher_obj: Gtk.UriLauncher, result: Gio.AsyncResult) -> None:
                try:
                    ok = launcher_obj.launch_finish(result)
                    if not ok:
                        self._launch_uri_fallback(uri, path)
                except Exception:
                    self._launch_uri_fallback(uri, path)

            launcher.launch(self, None, done)
            return

        self._launch_uri_fallback(uri, path)

    def _launch_uri_fallback(self, uri: str, path: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
            return
        except Exception:
            pass

        try:
            subprocess.run(["xdg-open", uri], check=True)
            return
        except Exception as exc:
            self._set_status(f"Open failed: {path} — {exc}")

    def _on_copy_text_number(self, *_args) -> None:
        CopyByNumber.on_copy_text_number(self)

    def _on_copy_regex_pattern(self, *_args) -> None:
        pattern = r"\A(?=[\s\S]*HELPER_PATTERN)[\s\S]*?(TARGET_PATTERN)"
        CopyByNumber._copy_text_async(self, pattern, verify=False)

    def _on_about(self, *_args) -> None:
        about_window.show_about_default(
            self,
            app_id=APP_ID,
            app_name=APP_NAME,
            version=APP_VERSION,
        )

