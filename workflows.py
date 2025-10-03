from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable

from app_context import AppContext
from pdfoutline_mod import pdfoutline
import toc_creator


class WorkflowRunner:
    """Encapsulate long-running file operations so they can be tested independently."""

    def __init__(
        self,
        context: AppContext,
        set_status: Callable[[str], None],
        load_toc: Callable[[], None],
        reset_toc: Callable[[], None],
    ) -> None:
        self._ctx = context
        self._set_status = set_status
        self._load_toc = load_toc
        self._reset_toc = reset_toc

    # ── Input management ─────────────────────────────────────────────────--
    def clear_input(self) -> None:
        """Clear the Input directory and transient artifacts."""
        try:
            Path(self._ctx.input_folder).mkdir(parents=True, exist_ok=True)
            for entry in list(Path(self._ctx.input_folder).iterdir()):
                try:
                    if entry.is_file() or entry.is_symlink():
                        entry.unlink()
                    else:
                        shutil.rmtree(entry)
                except Exception:
                    pass

            try:
                for entry in list(Path(self._ctx.shm_text_dir).iterdir()):
                    try:
                        if entry.is_file() or entry.is_symlink():
                            entry.unlink()
                        else:
                            shutil.rmtree(entry)
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                if os.path.exists(self._ctx.combined_pdf_path):
                    os.remove(self._ctx.combined_pdf_path)
            except Exception:
                pass

            self._reset_toc()
            self._load_toc()
            self._set_status("Input cleared. TOC & text pages reset.")
        except Exception as exc:
            self._set_status(f"Clear Input failed: {exc}")

    def copy_pdfs_into_input(self, file_paths: Iterable[str]) -> None:
        try:
            Path(self._ctx.input_folder).mkdir(parents=True, exist_ok=True)
            count = 0
            for path in file_paths:
                if not path or not path.lower().endswith(".pdf"):
                    continue
                try:
                    dst = os.path.join(self._ctx.input_folder, os.path.basename(path))
                    shutil.copy2(path, dst)
                    count += 1
                except Exception:
                    pass

            if count == 0:
                self._set_status("No PDFs added.")
            else:
                self._set_status(f"Added {count} PDF(s) to Input.")
        except Exception as exc:
            self._set_status(f"Add PDFs failed: {exc}")

    # ── TOC + bookmark generation ─────────────────────────────────────────
    def create_toc(self) -> None:
        for folder in (
            self._ctx.shm_root,
            self._ctx.shm_text_dir,
            self._ctx.shm_toc_dir,
            self._ctx.input_folder,
        ):
            Path(folder).mkdir(parents=True, exist_ok=True)

        try:
            if os.path.exists(self._ctx.combined_pdf_path):
                os.remove(self._ctx.combined_pdf_path)
        except Exception:
            pass

        try:
            text_dir = Path(self._ctx.shm_text_dir)
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

        toc_creator.create_toc(
            text_record_folder=self._ctx.shm_text_dir,
            input_folder=self._ctx.input_folder,
            combined_pdf_path=self._ctx.combined_pdf_path,
            toc_file=self._ctx.toc_file_path,
            regexes_folder=self._ctx.user_regex_dir,
            update_progress=lambda fraction: None,
        )

        self._load_toc()

    def create_bookmarks(self) -> None:
        pdfoutline(
            inpdf=self._ctx.combined_pdf_path,
            tocfile=self._ctx.toc_file_path,
            outpdf=self._ctx.completed_record_pdf,
            update_progress=lambda fraction: None,
        )

    # ── Scripting helpers ─────────────────────────────────────────────────
    def run_script_in_toc_dir(self, script_path: str) -> None:
        try:
            ext = os.path.splitext(script_path)[1].lower()
            if ext == ".sh":
                cmd = ["bash", script_path]
            elif ext == ".py":
                cmd = [sys.executable, script_path]
            else:
                cmd = [script_path]

            env = dict(os.environ)
            env["DOGEAR_TOC"] = self._ctx.toc_file_path
            env["DOGEAR_TEXTDIR"] = self._ctx.shm_text_dir

            process = subprocess.run(
                cmd,
                cwd=self._ctx.shm_toc_dir,
                capture_output=True,
                text=True,
                env=env,
            )
            if process.returncode != 0:
                raise RuntimeError(
                    f"exit {process.returncode}. stderr:\n{process.stderr or '(none)'}"
                )

            self._load_toc()
            self.mirror_tree(self._ctx.shm_text_dir, self._ctx.host_view_text)

            output = (process.stdout or "").strip()
            base = os.path.basename(script_path)
            self._set_status(
                f"Script '{base}' completed." + (f" Output: {output}" if output else "")
            )
        except Exception as exc:
            self._set_status(f"Script failed: {exc}")

    # ── File utilities ─────────────────────────────────────────────────────
    def mirror_tree(self, src: str, dst: str) -> None:
        try:
            os.makedirs(dst, exist_ok=True)
            for name in os.listdir(dst):
                target = os.path.join(dst, name)
                try:
                    shutil.rmtree(target) if os.path.isdir(target) else os.remove(target)
                except Exception:
                    pass
            for name in os.listdir(src):
                source = os.path.join(src, name)
                target = os.path.join(dst, name)
                try:
                    if os.path.isdir(source):
                        shutil.copytree(source, target)
                    else:
                        shutil.copy2(source, target)
                except Exception:
                    pass
        except Exception as exc:
            self._set_status(f"Mirror failed: {exc}")

