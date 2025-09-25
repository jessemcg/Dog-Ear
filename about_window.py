#!/usr/bin/env python3
# about_window.py — GTK4/Libadwaita About window for Dog Ear

from __future__ import annotations
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

# --- at the top of about_window.py, after the imports ---

WEBSITE = "https://github.com/jessemcg/Dog-Ear"
ISSUE_URL = "https://github.com/jessemcg/Dog-Ear/issues"
DEVELOPERS = ["Jesse McGowan"]
COPYRIGHT = "© 2025 Jesse McGowan"

# AppStream-compatible markup: <p>, <ul>/<li>, <ol>/<li>, inline <em>, <code> (no <a>)
RELEASE_NOTES = (
    "<ul>"
    "<li>Initial public release of Dog Ear with TOC creation and PDF bookmarking.</li>"
    "<li>GTK4 + Libadwaita UI with streamlined workflow.</li>"
    "<li>Access to text pages for regex testing and an in-app TOC editor.</li>"
    "<li>Option to copy specific text pages or regex template to clipboard.</li>"
    "<li>Optional post-processing hook (.sh or .py) for the TOC.</li>"
    "</ul>"
)

DEFAULTS = dict(
    website=WEBSITE,
    issue_url=ISSUE_URL,
    developers=DEVELOPERS,
    release_notes=RELEASE_NOTES,
    copyright=COPYRIGHT,
)

def show_about_default(parent, *, app_id: str, app_name: str, version: str):
    # Uses module defaults for everything else
    return show_about(
        parent,
        app_id=app_id,
        app_name=app_name,
        version=version,
        **DEFAULTS,
    )

def show_about(
    parent,
    *,
    app_id: str,
    app_name: str,
    version: str,
    website: str,
    issue_url: str,
    developers: list[str] | None = None,
    release_notes: str | None = None,   # Expect AppStream markup string
    copyright: str | None = None,
):
    """Show the Dog Ear About window.

    release_notes:
        Provide an AppStream-compatible markup string using only:
        <p>, <ul>/<li>, <ol>/<li>, with inline <em> and <code>.
        (No <a> links in release notes.)
    """
    win = Adw.AboutWindow(transient_for=parent, modal=True)

    # Identity
    win.set_application_name(app_name)
    win.set_application_icon(app_id)  # must match your installed icon name
    win.set_version(version)

    # Developer / Credits
    devs = developers or []
    if devs:
        win.set_developer_name(", ".join(devs))
        win.set_developers(devs)
    else:
        win.set_developer_name("")

    # Links
    win.set_website(website)
    win.set_issue_url(issue_url)

    # What's New (expects AppStream markup; do not pass plain text/Markdown)
    if release_notes:
        win.set_release_notes(release_notes)
        win.set_release_notes_version(version)

    # Legal — Pango markup is allowed here; links are clickable.
    win.set_license_type(Gtk.License.GPL_3_0_ONLY)
    win.set_license(
        'This program is licensed under the '
        '<a href="https://www.gnu.org/licenses/gpl-3.0.html">'
        'GNU General Public License, version 3 (GPL-3.0-only)</a>.'
    )

    if copyright:
        # AboutWindow exposes a "copyright" property
        win.set_property("copyright", copyright)

    win.present()

