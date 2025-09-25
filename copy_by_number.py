# copy_by_number.py
from __future__ import annotations
import os
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, GLib  # noqa: F401

class CopyByNumber:
    """Stateless helpers; methods expect a window 'self' that has _set_status()."""

    # ---------- path helpers (RESTORED) ----------
    @staticmethod
    def _resolve_text_pages_dir(self) -> str | None:
        """
        Find the directory where per-page text files live.
        Priority:
          1) self.text_record_folder (if your app sets it)
          2) global 'shm_text_dir' (if defined elsewhere)
          3) env PDFMARKER_TEXT_PAGES_DIR
          4) ~/Downloads/DogEar_TextPages  (fallback)
        """
        if hasattr(self, "text_record_folder"):
            return getattr(self, "text_record_folder")
        trf = globals().get("shm_text_dir")
        if isinstance(trf, str) and trf:
            return trf
        env_dir = os.environ.get("PDFMARKER_TEXT_PAGES_DIR")
        if env_dir:
            return env_dir
        return str(Path.home() / "Downloads" / "DogEar_TextPages")

    @staticmethod
    def _format_page_filename(self, n: int) -> str:
        """
        Format the file name for page 'n'.
        Respects PAGE_FILE_FMT (e.g., '{:04d}.txt') if defined; otherwise uses PAGE_PAD.
        """
        fmt = globals().get("PAGE_FILE_FMT")
        if isinstance(fmt, str) and "{" in fmt:
            return fmt.format(n)
        pad = globals().get("PAGE_PAD", 4)
        return f"{n:0{pad}d}.txt"

    @staticmethod
    def _text_path_for(self, n: int) -> str:
        base = CopyByNumber._resolve_text_pages_dir(self)
        return os.path.join(base, CopyByNumber._format_page_filename(self, n))

    # ---------- clipboard helper (Wayland-friendly) ----------
    @staticmethod
    def _copy_text_async(self, text: str, filename_for_status: str | None = None, verify: bool = True):
        """
        Copy to clipboard and (optionally) verify by reading back.
        If verify is False (or unavailable), show a simple 'Copied.' status.
        """
        # Prefer widget-owned clipboard (GTK4) then fall back to display clipboard
        cb = None
        try:
            cb = self.get_clipboard()
        except Exception:
            pass
        if cb is None:
            try:
                disp = Gdk.Display.get_default()
                if disp is not None:
                    cb = disp.get_clipboard()
            except Exception:
                cb = None

        if cb is None:
            self._set_status("Copy failed: clipboard not available.")
            return

        def _do_copy():
            try:
                provider = Gdk.ContentProvider.new_for_value(text)
                cb.set_content(provider)
            except Exception:
                try:
                    cb.set_text(text)
                except Exception as e:
                    self._set_status(f"Copy failed: {e}")
                    return False

            if not verify:
                self._set_status("Copied.")
                return False

            def _on_readback(_cb, res, _data):
                try:
                    got = _cb.read_text_finish(res)
                except Exception:
                    self._set_status("Copied.")
                    return
                if (got or "") == text:
                    self._set_status(
                        f"Copied {filename_for_status} to clipboard." if filename_for_status else "Copied."
                    )
                else:
                    self._set_status("Copied (content mismatch on verify).")

            try:
                cb.read_text_async(priority=GLib.PRIORITY_DEFAULT,
                                   cancellable=None,
                                   callback=_on_readback,
                                   user_data=None)
            except Exception:
                self._set_status("Copied.")
            return False

        GLib.idle_add(_do_copy)

    # ---------- UI entry point ----------
    @staticmethod
    def on_copy_text_number(self, *_):
        dlg = Adw.MessageDialog.new(self, "Copy Page Text", None)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("copy", "Copy")
        dlg.set_default_response("copy")
        dlg.set_close_response("cancel")

        entry = Gtk.Entry()
        entry.set_placeholder_text("Page number")
        entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
        entry.set_activates_default(True)
        entry.set_width_chars(4)
        entry.set_max_length(6)
        entry.set_margin_top(6)
        entry.set_margin_bottom(6)
        entry.set_margin_start(10)
        entry.set_margin_end(10)
        entry.set_halign(Gtk.Align.CENTER)
        entry.connect("map", lambda *_: entry.grab_focus())
        dlg.set_extra_child(entry)

        def on_resp(d, resp):
            if resp != "copy":
                d.destroy()
                return

            txt = entry.get_text().strip()
            try:
                n = int(txt)
            except ValueError:
                self._set_status("Please enter a valid number.")
                d.destroy()
                return

            base = CopyByNumber._resolve_text_pages_dir(self)
            if not base or not os.path.isdir(base):
                self._set_status(f"Text-pages dir not found: {base!r}")
                d.destroy()
                return

            path = CopyByNumber._text_path_for(self, n)
            if not os.path.exists(path):
                pad = globals().get("PAGE_PAD", 4)
                self._set_status(f"No text file for {n:0{pad}d}.")
                d.destroy()
                return

            try:
                content = Path(path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                self._set_status(f"Could not read file: {e}")
                d.destroy()
                return

            d.destroy()
            # If you prefer no verify message: verify=False
            CopyByNumber._copy_text_async(self, content, os.path.basename(path), verify=False)

        dlg.connect("response", on_resp)
        dlg.present()

