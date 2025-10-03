#!/usr/bin/env python3
"""Application entrypoint for Dog Ear."""

from __future__ import annotations

import os
import sys

# Default to a GPU backend if the user hasn’t specified one
if "GSK_RENDERER" not in os.environ:
    os.environ["GSK_RENDERER"] = "opengl"

APP_DIR = os.path.abspath(os.path.dirname(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, Gtk

from app_context import APP_ID, DEBUG, dlog
from window import DogEarWindow


class DogEarApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win: Adw.ApplicationWindow | None = None

    def do_startup(self, *_args) -> None:
        dlog("do_startup()")
        Adw.Application.do_startup(self)
        try:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        except Exception as exc:
            dlog(f"StyleManager failed: {exc}")

    def do_activate(self, *_args) -> None:
        dlog("do_activate()")
        if not self.win:
            try:
                self.win = DogEarWindow(self)
                dlog("Main window constructed")
            except Exception as exc:
                dlog(f"Window init failed: {exc}")
                self.win = Adw.ApplicationWindow(application=self, title="Dog Ear (startup error)")
                box = Gtk.Box(
                    orientation=Gtk.Orientation.VERTICAL,
                    margin_top=24,
                    margin_bottom=24,
                    margin_start=24,
                    margin_end=24,
                    spacing=12,
                )
                label = Gtk.Label(label=f"Startup error:\n{exc}", xalign=0)
                box.append(label)
                self.win.set_content(box)
        self.win.present()
        dlog("present() called")


def main() -> None:
    if DEBUG:
        try:
            import faulthandler
            import signal

            faulthandler.enable()
            faulthandler.register(signal.SIGUSR2)
        except Exception:
            pass
        dlog("Starting DogEarApp()")

    try:
        DogEarApp().run(None)
    except Exception as exc:
        dlog(f"FATAL: {exc}")
        raise


if __name__ == "__main__":
    main()
