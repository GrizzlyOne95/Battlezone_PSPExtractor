#!/usr/bin/env python3
"""
Battlezone PSP Extractor GUI.

Simplified workflow:
- Select one input path (ISO image, extracted ISO root, PSP_GAME, or USRDIR)
- Select one output root directory
- Run models/audio/etc. without per-tool path setup
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path, PurePosixPath
from tkinter import filedialog, messagebox, ttk
from typing import Callable

try:
    from PIL import Image, ImageTk
except Exception:
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]

BZ_BG = "#0b0f0b"
BZ_FG = "#d7e0d7"
BZ_GREEN = "#5cff72"
BZ_ACCENT = "#00d9ff"
BZ_MUTED = "#93a793"
BZ_DARK = "#233223"

CONFIG_FILE = "bzpsp_gui_config.json"


def _load_embedded_extractor_module(extractor_name: str):
    key = Path(extractor_name).name.lower()
    if key == "extract_psp_txd_textures.py":
        from extractors import extract_psp_txd_textures as mod  # type: ignore
        return mod
    if key == "extract_psp_rws_geometry.py":
        from extractors import extract_psp_rws_geometry as mod  # type: ignore
        return mod
    if key == "extract_psp_audio.py":
        from extractors import extract_psp_audio as mod  # type: ignore
        return mod
    if key == "extract_psp_lvl_json.py":
        from extractors import extract_psp_lvl_json as mod  # type: ignore
        return mod
    if key == "extract_psp_movies.py":
        from extractors import extract_psp_movies as mod  # type: ignore
        return mod
    if key == "extract_psp_data_tables.py":
        from extractors import extract_psp_data_tables as mod  # type: ignore
        return mod
    if key == "extract_psp_font_metrics.py":
        from extractors import extract_psp_font_metrics as mod  # type: ignore
        return mod
    raise ValueError(f"Unsupported extractor: {extractor_name}")


def run_embedded_extractor(extractor_name: str, extractor_args: list[str]) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)

    try:
        mod = _load_embedded_extractor_module(extractor_name)
    except Exception as exc:
        print(f"Extractor load failed: {exc}", file=sys.stderr)
        return 2

    if not hasattr(mod, "main"):
        print(f"Extractor has no main(): {extractor_name}", file=sys.stderr)
        return 2

    old_argv = sys.argv[:]
    sys.argv = [extractor_name, *extractor_args]
    try:
        code = mod.main()
    except SystemExit as exc:
        raw = exc.code
        if raw is None:
            return 0
        if isinstance(raw, int):
            return raw
        return 1
    finally:
        sys.argv = old_argv

    return int(code) if isinstance(code, int) else 0


def _load_windows_font(font_path: Path) -> bool:
    if sys.platform != "win32" or not font_path.exists():
        return False
    try:
        FR_PRIVATE = 0x10
        added = ctypes.windll.gdi32.AddFontResourceExW(str(font_path), FR_PRIVATE, 0)
        return bool(added > 0)
    except Exception:
        return False


def _pick_font(root: tk.Tk) -> str:
    try:
        families = {f.lower(): f for f in tkfont.families(root)}
    except Exception:
        return "Consolas"
    for wanted in ("fff estudio", "fffestudio", "battle", "coulson"):
        for low, original in families.items():
            if wanted in low:
                return original
    return "Consolas"


class BZPSPGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.repo_root = Path(__file__).resolve().parents[1]
        self.bz_root = self.repo_root.parent
        self.extractors_root = self._find_extractors_root()
        self.vendor_dragonff = self._find_vendor_dragonff()
        self.config_path = self.repo_root / CONFIG_FILE

        self.config = self._load_config()
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.current_proc: subprocess.Popen[str] | None = None

        self.app_icon_path = self._find_asset_path("038_PU_Ammo_big.png")
        self.app_icon_photo: tk.PhotoImage | None = None
        self.bg_image_path = self._find_asset_path("background.jpg")
        self.bg_label: tk.Label | None = None
        self.bg_source = None
        self.bg_photo = None
        self.bg_resize_after_id: str | None = None

        self._load_fonts()
        self.font_family = _pick_font(root)
        self._setup_window()
        self._setup_background()
        self._setup_style()
        self._build_vars()
        self._build_ui()
        self._apply_config()
        self._recompute_paths()
        self._drain_log_queue()

    def _find_asset_path(self, name: str) -> Path:
        candidates: list[Path] = []

        meipass = getattr(sys, "_MEIPASS", None)
        if isinstance(meipass, str) and meipass:
            candidates.append(Path(meipass) / name)

        app_dir = Path(__file__).resolve().parent
        candidates.extend(
            [
                app_dir / name,
                self.repo_root / name,
                self.repo_root / "app" / name,
                Path.cwd() / name,
            ]
        )

        for cand in candidates:
            if cand.exists():
                return cand
        return self.repo_root / name

    def _candidate_roots(self) -> list[Path]:
        roots: list[Path] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if isinstance(meipass, str) and meipass:
            roots.append(Path(meipass))
        roots.extend([self.repo_root, self.repo_root.parent, Path.cwd()])

        out: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root.resolve()) if root.exists() else str(root)
            if key in seen:
                continue
            seen.add(key)
            out.append(root)
        return out

    def _find_extractors_root(self) -> Path:
        for root in self._candidate_roots():
            cand = root / "extractors"
            if cand.is_dir():
                return cand
        return self.repo_root / "extractors"

    def _find_vendor_dragonff(self) -> Path:
        for root in self._candidate_roots():
            cand = root / "vendor" / "DragonFF"
            if cand.is_dir():
                return cand
        return self.repo_root / "vendor" / "DragonFF"

    def _load_fonts(self) -> None:
        candidates = [
            self.bz_root / "Font" / "FFFEstudioExtended" / "FFFEstudioExtended.ttf",
            self.bz_root / "Font" / "FFFEstudioExtended" / "web" / "font" / "FFFEstudioExtended.ttf",
            self.repo_root / "app" / "FFFEstudioExtended.ttf",
        ]
        for fp in candidates:
            _load_windows_font(fp)

    def _setup_window(self) -> None:
        self.root.title("Battlezone PSP Extractor")
        self.root.geometry("1380x980")
        self.root.minsize(1080, 740)
        self.root.configure(bg=BZ_BG)
        self._setup_window_icon()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_window_icon(self) -> None:
        if not self.app_icon_path.exists():
            self._log("Window icon not found.")
            return
        try:
            self.app_icon_photo = tk.PhotoImage(file=str(self.app_icon_path))
            self.root.iconphoto(True, self.app_icon_photo)
        except Exception as exc:
            self._log(f"Window icon load failed: {exc}")

    def _setup_background(self) -> None:
        self.bg_label = tk.Label(self.root, bg=BZ_BG, bd=0, highlightthickness=0)
        self.bg_label.place(x=0, y=0, relwidth=1, relheight=1)

        if Image is None or ImageTk is None:
            self._log("Background disabled: Pillow not available.")
            return
        if self.bg_image_path is None or not self.bg_image_path.exists():
            self._log("Background image not found.")
            return

        try:
            self.bg_source = Image.open(self.bg_image_path)
            self._refresh_background_image()
            self.root.bind("<Configure>", self._on_root_resize, add="+")
        except Exception as exc:
            self._log(f"Background load failed: {exc}")

    def _refresh_background_image(self) -> None:
        if self.bg_source is None or self.bg_label is None or ImageTk is None:
            return
        w = max(1, self.root.winfo_width())
        h = max(1, self.root.winfo_height())
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        resized = self.bg_source.resize((w, h), resample)
        self.bg_photo = ImageTk.PhotoImage(resized)
        self.bg_label.configure(image=self.bg_photo)
        self.bg_label.lower()

    def _on_root_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root or self.bg_source is None:
            return
        if self.bg_resize_after_id is not None:
            try:
                self.root.after_cancel(self.bg_resize_after_id)
            except Exception:
                pass
        self.bg_resize_after_id = self.root.after(50, self._refresh_background_image)

    def _setup_style(self) -> None:
        style = ttk.Style()
        style.theme_use("default")
        normal = (self.font_family, 10)
        bold = (self.font_family, 11, "bold")

        style.configure(".", foreground=BZ_FG, font=normal)
        style.configure("TFrame")
        style.configure("Panel.TFrame")
        style.configure("TLabel", foreground=BZ_FG)
        style.configure("Header.TLabel", font=(self.font_family, 16, "bold"), foreground=BZ_GREEN)
        style.configure("Sub.TLabel", foreground=BZ_MUTED)
        style.configure("TLabelframe", foreground=BZ_ACCENT)
        style.configure("TLabelframe.Label", foreground=BZ_ACCENT, font=bold)
        style.configure("TEntry", fieldbackground="#172217", foreground=BZ_ACCENT)
        style.configure("TButton", background=BZ_DARK, foreground=BZ_FG)
        style.map("TButton", background=[("active", "#1d2a1d")], foreground=[("active", BZ_GREEN)])
        style.configure("Action.TButton", foreground=BZ_GREEN, font=bold)
        style.configure("Warn.TButton", foreground="#ffb347")
        style.configure("TNotebook", background=BZ_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#132013", foreground=BZ_FG, padding=[10, 4])
        style.map("TNotebook.Tab", background=[("selected", "#1f311f")], foreground=[("selected", BZ_GREEN)])
        style.configure("TCombobox", fieldbackground="#172217", foreground=BZ_ACCENT)

    def _build_vars(self) -> None:
        self.var_input_root = tk.StringVar(value="")
        self.var_output_root = tk.StringVar(value="")

        self.var_geo_mode = tk.StringVar(value="all")
        self.var_geo_limit = tk.StringVar(value="0")

        self.var_audio_mode = tk.StringVar(value="all")
        self.var_audio_decode_vag = tk.BooleanVar(value=True)

        self.var_lvl_limit = tk.StringVar(value="0")

        self.var_movie_mode = tk.StringVar(value="all")
        self.var_movie_overwrite = tk.BooleanVar(value=True)
        self.var_ffmpeg = tk.StringVar(value=self._find_tool_executable("ffmpeg"))
        self.var_ffprobe = tk.StringVar(value=self._find_tool_executable("ffprobe"))

        self.var_usrdir_status = tk.StringVar(value="USRDIR: (not resolved)")
        self.var_status = tk.StringVar(value="Idle")
        self.run_buttons: list[ttk.Button] = []

        self.paths: dict[str, Path] = {}
        self.var_input_root.trace_add("write", lambda *_: self._recompute_paths())
        self.var_output_root.trace_add("write", lambda *_: self._recompute_paths())

    def _find_tool_executable(self, name: str) -> str:
        names = [name]
        if sys.platform == "win32" and not name.lower().endswith(".exe"):
            names.insert(0, f"{name}.exe")

        candidates: list[Path] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if isinstance(meipass, str) and meipass:
            base = Path(meipass)
            for n in names:
                candidates.append(base / n)

        exe_dir = Path(sys.executable).resolve().parent
        for n in names:
            candidates.append(exe_dir / n)
            candidates.append(Path.cwd() / n)
            candidates.append(self.repo_root / n)

        for cand in candidates:
            if cand.exists() and cand.is_file():
                return str(cand)
        return name

    def _build_ui(self) -> None:
        wrap = ttk.Frame(self.root)
        wrap.pack(fill="both", expand=True, padx=10, pady=10)

        head = ttk.Frame(wrap, style="Panel.TFrame")
        head.pack(fill="x", padx=10, pady=(8, 6))
        ttk.Label(head, text="Battlezone PSP Extractor", style="Header.TLabel").pack(side="left")
        ttk.Label(head, text="Select input + output, then run extractors", style="Sub.TLabel").pack(side="left", padx=(12, 0))

        io_frame = ttk.LabelFrame(wrap, text="Input / Output", style="TLabelframe")
        io_frame.pack(fill="x", padx=10, pady=(4, 8))
        self._add_io_row(io_frame, "Input Root", self.var_input_root)
        self._add_io_row(io_frame, "Output Root", self.var_output_root, browse_iso=False)

        ttk.Label(io_frame, textvariable=self.var_usrdir_status, foreground=BZ_ACCENT).pack(anchor="w", padx=8, pady=(0, 6))

        bar = ttk.Frame(wrap, style="Panel.TFrame")
        bar.pack(fill="x", padx=10, pady=(0, 8))
        btn_all = ttk.Button(bar, text="Run All", style="Action.TButton", command=self.run_all)
        btn_all.pack(side="left")
        self.run_buttons.append(btn_all)
        btn_stop = ttk.Button(bar, text="Stop", style="Warn.TButton", command=self.stop_running)
        btn_stop.pack(side="left", padx=(8, 0))
        ttk.Label(bar, textvariable=self.var_status, foreground=BZ_ACCENT).pack(side="right")

        notebook = ttk.Notebook(wrap)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        tabs = {
            "Textures": ttk.Frame(notebook, style="Panel.TFrame"),
            "Geometry": ttk.Frame(notebook, style="Panel.TFrame"),
            "Audio": ttk.Frame(notebook, style="Panel.TFrame"),
            "Levels": ttk.Frame(notebook, style="Panel.TFrame"),
            "Movies": ttk.Frame(notebook, style="Panel.TFrame"),
            "Data": ttk.Frame(notebook, style="Panel.TFrame"),
            "Fonts": ttk.Frame(notebook, style="Panel.TFrame"),
        }
        for name, frame in tabs.items():
            notebook.add(frame, text=name)

        self._build_textures_tab(tabs["Textures"])
        self._build_geometry_tab(tabs["Geometry"])
        self._build_audio_tab(tabs["Audio"])
        self._build_levels_tab(tabs["Levels"])
        self._build_movies_tab(tabs["Movies"])
        self._build_data_tab(tabs["Data"])
        self._build_fonts_tab(tabs["Fonts"])

        log_frame = ttk.LabelFrame(wrap, text="Log", style="TLabelframe")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text = tk.Text(
            log_frame,
            height=14,
            bg="#050905",
            fg=BZ_GREEN,
            insertbackground=BZ_GREEN,
            relief="flat",
            font=(self.font_family, 10),
        )
        self.log_text.pack(fill="both", expand=True, padx=8, pady=8)
    def _add_io_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, browse_iso: bool = True) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text=label, width=16).pack(side="left")
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Dir", command=lambda: self._browse_dir(var)).pack(side="left", padx=(6, 0))
        if browse_iso:
            ttk.Button(row, text="ISO", command=lambda: self._browse_iso(var)).pack(side="left", padx=(6, 0))

    def _add_path_info(self, parent: ttk.Frame, text: str) -> None:
        ttk.Label(parent, text=text, style="Sub.TLabel").pack(anchor="w", padx=8, pady=(0, 6))

    def _build_textures_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="TXD -> PNG", style="TLabelframe")
        frame.pack(fill="x", padx=10, pady=10)
        self._add_path_info(frame, "Input: <USRDIR>/textures")
        self._add_path_info(frame, "Output: <Output Root>/textures_png")
        btn = ttk.Button(frame, text="Run Texture Extract", style="Action.TButton", command=self.run_textures)
        btn.pack(anchor="w", padx=8, pady=8)
        self.run_buttons.append(btn)

    def _build_geometry_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="RWS -> OBJ", style="TLabelframe")
        frame.pack(fill="x", padx=10, pady=10)
        self._add_path_info(frame, "Input: <USRDIR>/models + <USRDIR>/terrains")
        self._add_path_info(frame, "Output: <Output Root>/rws_obj")

        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Mode", width=16).pack(side="left")
        ttk.Combobox(
            row,
            textvariable=self.var_geo_mode,
            values=["all", "models", "terrains"],
            width=12,
            state="readonly",
        ).pack(side="left")
        ttk.Label(row, text="Limit", width=10).pack(side="left", padx=(12, 0))
        ttk.Entry(row, textvariable=self.var_geo_limit, width=10).pack(side="left")

        btn = ttk.Button(frame, text="Run Geometry Extract", style="Action.TButton", command=self.run_geometry)
        btn.pack(anchor="w", padx=8, pady=8)
        self.run_buttons.append(btn)

    def _build_audio_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Audio Rip", style="TLabelframe")
        frame.pack(fill="x", padx=10, pady=10)
        self._add_path_info(frame, "Input: <USRDIR>/audio")
        self._add_path_info(frame, "Output: <Output Root>/audio_rip")

        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Mode", width=16).pack(side="left")
        ttk.Combobox(
            row,
            textvariable=self.var_audio_mode,
            values=["all", "at3", "bnk"],
            width=12,
            state="readonly",
        ).pack(side="left")
        ttk.Checkbutton(row, text="Decode VAG to WAV", variable=self.var_audio_decode_vag).pack(
            side="left", padx=(12, 0)
        )

        btn = ttk.Button(frame, text="Run Audio Extract", style="Action.TButton", command=self.run_audio)
        btn.pack(anchor="w", padx=8, pady=8)
        self.run_buttons.append(btn)

    def _build_levels_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="LVL -> JSON", style="TLabelframe")
        frame.pack(fill="x", padx=10, pady=10)
        self._add_path_info(frame, "Input: <USRDIR>/leveldata")
        self._add_path_info(frame, "Output: <Output Root>/leveldata_json")

        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Limit", width=16).pack(side="left")
        ttk.Entry(row, textvariable=self.var_lvl_limit, width=10).pack(side="left")

        btn = ttk.Button(frame, text="Run LVL Extract", style="Action.TButton", command=self.run_levels)
        btn.pack(anchor="w", padx=8, pady=8)
        self.run_buttons.append(btn)

    def _build_movies_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="PMF Movies", style="TLabelframe")
        frame.pack(fill="x", padx=10, pady=10)
        self._add_path_info(frame, "Input: <USRDIR>/movie")
        self._add_path_info(frame, "Output: <Output Root>/movies")

        row = ttk.Frame(frame)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="Mode", width=16).pack(side="left")
        ttk.Combobox(
            row,
            textvariable=self.var_movie_mode,
            values=["all", "copy", "probe", "transcode"],
            width=12,
            state="readonly",
        ).pack(side="left")
        ttk.Checkbutton(row, text="Overwrite MP4", variable=self.var_movie_overwrite).pack(side="left", padx=(12, 0))

        row2 = ttk.Frame(frame)
        row2.pack(fill="x", padx=8, pady=4)
        ttk.Label(row2, text="ffmpeg", width=16).pack(side="left")
        ttk.Entry(row2, textvariable=self.var_ffmpeg, width=22).pack(side="left")
        ttk.Label(row2, text="ffprobe", width=12).pack(side="left", padx=(12, 0))
        ttk.Entry(row2, textvariable=self.var_ffprobe, width=22).pack(side="left")

        btn = ttk.Button(frame, text="Run Movie Extract", style="Action.TButton", command=self.run_movies)
        btn.pack(anchor="w", padx=8, pady=8)
        self.run_buttons.append(btn)

    def _build_data_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Data Tables -> JSON", style="TLabelframe")
        frame.pack(fill="x", padx=10, pady=10)
        self._add_path_info(frame, "Input: <USRDIR>/leveldata + text + menu")
        self._add_path_info(frame, "Output: <Output Root>/data_tables_json")
        btn = ttk.Button(frame, text="Run Data Extract", style="Action.TButton", command=self.run_data_tables)
        btn.pack(anchor="w", padx=8, pady=8)
        self.run_buttons.append(btn)

    def _build_fonts_tab(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Font Metrics -> JSON", style="TLabelframe")
        frame.pack(fill="x", padx=10, pady=10)
        self._add_path_info(frame, "Input: <USRDIR>/font")
        self._add_path_info(frame, "Output: <Output Root>/font_metrics_json")
        btn = ttk.Button(frame, text="Run Font Extract", style="Action.TButton", command=self.run_fonts)
        btn.pack(anchor="w", padx=8, pady=8)
        self.run_buttons.append(btn)

    def _browse_dir(self, var: tk.StringVar) -> None:
        value = filedialog.askdirectory()
        if value:
            var.set(value)

    def _browse_iso(self, var: tk.StringVar) -> None:
        value = filedialog.askopenfilename(filetypes=[("ISO image", "*.iso"), ("All files", "*.*")])
        if value:
            var.set(value)

    def _resolve_usrdir(self, input_root: Path) -> tuple[Path | None, str]:
        if input_root.is_file():
            if input_root.suffix.lower() == ".iso":
                return None, "ISO selected. USRDIR will be extracted automatically when you run."
            return None, f"Input is a file: {input_root.name}. Select a directory."

        if not input_root.exists():
            return None, "Input path does not exist."

        p = input_root
        if p.name.upper() == "USRDIR":
            return p, ""
        if (p / "USRDIR").is_dir():
            return p / "USRDIR", ""
        if (p / "PSP_GAME" / "USRDIR").is_dir():
            return p / "PSP_GAME" / "USRDIR", ""

        return None, "Could not resolve USRDIR from input path."

    @staticmethod
    def _strip_iso_version(name: str) -> str:
        return name.split(";", 1)[0]

    def _extract_usrdir_from_iso(self, iso_path: Path, cache_usrdir: Path) -> None:
        try:
            import pycdlib  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "ISO support requires pycdlib. Install dependencies with: pip install -r requirements.txt"
            ) from exc

        iso = pycdlib.PyCdlib()
        mode: str | None = None
        base_path: str | None = None
        iso.open(str(iso_path))
        try:
            try:
                iso.get_record(joliet_path="/PSP_GAME/USRDIR")
                mode = "joliet"
                base_path = "/PSP_GAME/USRDIR"
            except Exception:
                iso.get_record(iso_path="/PSP_GAME/USRDIR")
                mode = "iso"
                base_path = "/PSP_GAME/USRDIR"

            assert mode is not None
            assert base_path is not None
            key = f"{mode}_path"

            for current, dir_list, file_list in iso.walk(**{key: base_path}):
                current_path = PurePosixPath(current)
                rel = current_path.relative_to(PurePosixPath(base_path))
                local_dir = cache_usrdir / Path(*rel.parts) if rel.parts else cache_usrdir
                local_dir.mkdir(parents=True, exist_ok=True)

                for dirname in dir_list:
                    (local_dir / self._strip_iso_version(dirname)).mkdir(parents=True, exist_ok=True)

                for filename in file_list:
                    local_name = self._strip_iso_version(filename)
                    local_file = local_dir / local_name
                    local_file.parent.mkdir(parents=True, exist_ok=True)
                    iso_file = f"{current.rstrip('/')}/{filename}"
                    iso.get_file_from_iso(local_path=str(local_file), **{key: iso_file})
        finally:
            iso.close()

    def _prepare_iso_usrdir(self, iso_file: Path, out_root: Path) -> tuple[Path | None, str]:
        try:
            st = iso_file.stat()
        except Exception as exc:
            return None, f"Unable to read ISO metadata: {exc}"

        key_src = f"{iso_file.resolve()}::{st.st_size}::{st.st_mtime_ns}"
        key = hashlib.sha1(key_src.encode("utf-8")).hexdigest()[:16]
        cache_root = out_root / ".bzpsp_iso_cache" / key
        cache_usrdir = cache_root / "PSP_GAME" / "USRDIR"
        marker = cache_root / ".source_iso.txt"

        if cache_usrdir.is_dir():
            return cache_usrdir, ""

        if cache_root.exists():
            shutil.rmtree(cache_root, ignore_errors=True)
        cache_usrdir.mkdir(parents=True, exist_ok=True)

        self._log(f"Preparing ISO input: {iso_file}")
        self.var_status.set("Extracting ISO...")
        try:
            self._extract_usrdir_from_iso(iso_file, cache_usrdir)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(iso_file), encoding="utf-8")
            self._log(f"ISO extraction complete: {cache_usrdir}")
            return cache_usrdir, ""
        except Exception as exc:
            shutil.rmtree(cache_root, ignore_errors=True)
            return None, f"ISO extraction failed: {exc}"
        finally:
            self.var_status.set("Idle")

    def _set_paths_from_usrdir(self, usrdir: Path, out_root: Path) -> None:
        self.paths = {
            "usrdir": usrdir,
            "txd_root": usrdir / "textures",
            "models_root": usrdir / "models",
            "terrains_root": usrdir / "terrains",
            "audio_root": usrdir / "audio",
            "lvl_root": usrdir / "leveldata",
            "movie_root": usrdir / "movie",
            "data_leveldata_root": usrdir / "leveldata",
            "data_text_root": usrdir / "text",
            "data_menu_root": usrdir / "menu",
            "font_root": usrdir / "font",
            "txd_out": out_root / "textures_png",
            "geo_out": out_root / "rws_obj",
            "audio_out": out_root / "audio_rip",
            "lvl_out": out_root / "leveldata_json",
            "movie_out": out_root / "movies",
            "data_out": out_root / "data_tables_json",
            "font_out": out_root / "font_metrics_json",
        }

    def _recompute_paths(self) -> None:
        input_text = self.var_input_root.get().strip()
        self.paths.clear()

        if not input_text:
            self.var_usrdir_status.set("USRDIR: (input not set)")
            return

        out_text = self.var_output_root.get().strip()
        if not out_text:
            self.var_usrdir_status.set("USRDIR: waiting for output root")
            return

        in_path = Path(input_text)
        if in_path.is_file() and in_path.suffix.lower() == ".iso":
            self.var_usrdir_status.set("USRDIR: ISO selected (will extract on run)")
            return

        usrdir, err = self._resolve_usrdir(in_path)
        if usrdir is None:
            self.var_usrdir_status.set(f"USRDIR: unresolved ({err})")
            return

        out_root = Path(out_text)
        self._set_paths_from_usrdir(usrdir, out_root)
        _load_windows_font(usrdir / "font" / "FFFEstudioExtended.ttf")
        self.var_usrdir_status.set(f"USRDIR: {usrdir}")
    def _apply_config(self) -> None:
        if not isinstance(self.config, dict):
            return

        geom = self.config.get("window_geometry")
        if isinstance(geom, str) and geom:
            try:
                self.root.geometry(geom)
            except Exception:
                pass

        mapping = {
            "input_root": self.var_input_root,
            "output_root": self.var_output_root,
            "geo_mode": self.var_geo_mode,
            "geo_limit": self.var_geo_limit,
            "audio_mode": self.var_audio_mode,
            "lvl_limit": self.var_lvl_limit,
            "movie_mode": self.var_movie_mode,
            "ffmpeg": self.var_ffmpeg,
            "ffprobe": self.var_ffprobe,
        }
        for key, var in mapping.items():
            value = self.config.get(key)
            if isinstance(value, str) and value:
                var.set(value)

        for key, var in {
            "audio_decode_vag": self.var_audio_decode_vag,
            "movie_overwrite": self.var_movie_overwrite,
        }.items():
            value = self.config.get(key)
            if isinstance(value, bool):
                var.set(value)

    def _collect_config(self) -> dict:
        return {
            "window_geometry": self.root.geometry(),
            "input_root": self.var_input_root.get().strip(),
            "output_root": self.var_output_root.get().strip(),
            "geo_mode": self.var_geo_mode.get().strip(),
            "geo_limit": self.var_geo_limit.get().strip(),
            "audio_mode": self.var_audio_mode.get().strip(),
            "audio_decode_vag": bool(self.var_audio_decode_vag.get()),
            "lvl_limit": self.var_lvl_limit.get().strip(),
            "movie_mode": self.var_movie_mode.get().strip(),
            "movie_overwrite": bool(self.var_movie_overwrite.get()),
            "ffmpeg": self.var_ffmpeg.get().strip(),
            "ffprobe": self.var_ffprobe.get().strip(),
        }

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            return {}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_config(self) -> None:
        try:
            self.config_path.write_text(json.dumps(self._collect_config(), indent=2), encoding="utf-8")
        except Exception as exc:
            self._log(f"Warning: failed to save config: {exc}")

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Exit", "A task is still running. Stop and exit?"):
                return
            self.stop_running()
        self._save_config()
        self.root.destroy()

    def _log(self, msg: str) -> None:
        if not msg.endswith("\n"):
            msg += "\n"
        self.log_queue.put(msg)

    def _drain_log_queue(self) -> None:
        changed = False
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", msg)
            changed = True
        if changed:
            self.log_text.see("end")
        self.root.after(120, self._drain_log_queue)

    def _set_running(self, running: bool, status: str = "") -> None:
        state = "disabled" if running else "normal"
        for btn in self.run_buttons:
            btn.configure(state=state)
        self.var_status.set(status if status else ("Running" if running else "Idle"))

    def _extractor_script(self, name: str) -> Path:
        cand = self.extractors_root / name
        if cand.exists():
            return cand
        for root in self._candidate_roots():
            alt = root / "extractors" / name
            if alt.exists():
                return alt
        return cand

    def _python_executable(self) -> str:
        return str(Path(sys.executable).resolve())

    def _is_frozen(self) -> bool:
        return bool(getattr(sys, "frozen", False))

    def _build_extractor_cmd(self, extractor_script: str, args: list[str]) -> list[str]:
        base = [self._python_executable()]
        if self._is_frozen():
            return [*base, "--run-extractor", extractor_script, *args]
        return [*base, str(self._extractor_script(extractor_script)), *args]

    def _ensure_ready(self) -> bool:
        self._recompute_paths()

        input_text = self.var_input_root.get().strip()
        if not input_text:
            messagebox.showerror("Missing Input", "Select an input path first.")
            return False

        out_text = self.var_output_root.get().strip()
        if not out_text:
            messagebox.showerror("Missing Output", "Select an output root first.")
            return False

        in_path = Path(input_text)
        out_root = Path(out_text)
        if out_root.exists() and not out_root.is_dir():
            messagebox.showerror("Invalid Output", f"Output root is not a directory:\n{out_root}")
            return False

        if in_path.is_file() and in_path.suffix.lower() == ".iso":
            usrdir, err = self._prepare_iso_usrdir(in_path, out_root)
            if usrdir is None:
                messagebox.showerror("ISO Error", err or "Failed to prepare ISO input.")
                return False
            self._set_paths_from_usrdir(usrdir, out_root)
            self.var_usrdir_status.set(f"USRDIR: {usrdir}")
        elif not self.paths.get("usrdir"):
            messagebox.showerror("Invalid Input", "Could not resolve USRDIR from input path.")
            return False

        if not self.vendor_dragonff.exists():
            messagebox.showerror("Missing Dependency", f"DragonFF not found:\n{self.vendor_dragonff}")
            return False

        return True

    def _validate_cmd(self, cmd: list[str]) -> bool:
        if len(cmd) < 2:
            return False
        py = Path(cmd[0]).resolve()
        if not py.exists():
            messagebox.showerror("Invalid Path", f"Python not found:\n{py}")
            return False
        if len(cmd) >= 3 and cmd[1] == "--run-extractor":
            try:
                _load_embedded_extractor_module(cmd[2])
            except Exception as exc:
                messagebox.showerror("Invalid Extractor", str(exc))
                return False
        else:
            script = Path(cmd[1])
            if not script.exists():
                messagebox.showerror("Invalid Path", f"Script not found:\n{script}")
                return False
        return True

    def _build_textures_cmd(self) -> list[str]:
        return self._build_extractor_cmd(
            "extract_psp_txd_textures.py",
            [
            "--dragonff-root", str(self.vendor_dragonff),
            "--txd-root", str(self.paths["txd_root"]),
            "--out-root", str(self.paths["txd_out"]),
            ],
        )

    def _build_geometry_cmd(self) -> list[str]:
        cmd = self._build_extractor_cmd(
            "extract_psp_rws_geometry.py",
            [
            "--dragonff-root", str(self.vendor_dragonff),
            "--models-root", str(self.paths["models_root"]),
            "--terrains-root", str(self.paths["terrains_root"]),
            "--out-root", str(self.paths["geo_out"]),
            "--mode", self.var_geo_mode.get().strip() or "all",
            ],
        )
        try:
            limit = int((self.var_geo_limit.get() or "0").strip())
        except ValueError:
            limit = 0
        if limit > 0:
            cmd.extend(["--limit", str(limit)])
        return cmd

    def _build_audio_cmd(self) -> list[str]:
        cmd = self._build_extractor_cmd(
            "extract_psp_audio.py",
            [
            "--audio-root", str(self.paths["audio_root"]),
            "--out-root", str(self.paths["audio_out"]),
            "--mode", self.var_audio_mode.get().strip() or "all",
            ],
        )
        if not self.var_audio_decode_vag.get():
            cmd.append("--no-decode-vag")
        return cmd

    def _build_lvl_cmd(self) -> list[str]:
        cmd = self._build_extractor_cmd(
            "extract_psp_lvl_json.py",
            [
            "--lvl-root", str(self.paths["lvl_root"]),
            "--out-root", str(self.paths["lvl_out"]),
            ],
        )
        try:
            limit = int((self.var_lvl_limit.get() or "0").strip())
        except ValueError:
            limit = 0
        if limit > 0:
            cmd.extend(["--limit", str(limit)])
        return cmd

    def _build_movies_cmd(self) -> list[str]:
        cmd = self._build_extractor_cmd(
            "extract_psp_movies.py",
            [
            "--movie-root", str(self.paths["movie_root"]),
            "--out-root", str(self.paths["movie_out"]),
            "--mode", self.var_movie_mode.get().strip() or "all",
            "--ffmpeg", self.var_ffmpeg.get().strip() or "ffmpeg",
            "--ffprobe", self.var_ffprobe.get().strip() or "ffprobe",
            ],
        )
        if self.var_movie_overwrite.get():
            cmd.append("--overwrite")
        return cmd

    def _build_data_cmd(self) -> list[str]:
        return self._build_extractor_cmd(
            "extract_psp_data_tables.py",
            [
            "--leveldata-root", str(self.paths["data_leveldata_root"]),
            "--text-root", str(self.paths["data_text_root"]),
            "--menu-root", str(self.paths["data_menu_root"]),
            "--out-root", str(self.paths["data_out"]),
            ],
        )

    def _build_font_cmd(self) -> list[str]:
        return self._build_extractor_cmd(
            "extract_psp_font_metrics.py",
            [
            "--font-root", str(self.paths["font_root"]),
            "--out-root", str(self.paths["font_out"]),
            ],
        )
    def run_textures(self) -> None:
        self._start_pipeline([("Textures", self._build_textures_cmd)])

    def run_geometry(self) -> None:
        self._start_pipeline([("Geometry", self._build_geometry_cmd)])

    def run_audio(self) -> None:
        self._start_pipeline([("Audio", self._build_audio_cmd)])

    def run_levels(self) -> None:
        self._start_pipeline([("Levels", self._build_lvl_cmd)])

    def run_movies(self) -> None:
        self._start_pipeline([("Movies", self._build_movies_cmd)])

    def run_data_tables(self) -> None:
        self._start_pipeline([("Data", self._build_data_cmd)])

    def run_fonts(self) -> None:
        self._start_pipeline([("Fonts", self._build_font_cmd)])

    def run_all(self) -> None:
        self._start_pipeline(
            [
                ("Textures", self._build_textures_cmd),
                ("Geometry", self._build_geometry_cmd),
                ("Audio", self._build_audio_cmd),
                ("Levels", self._build_lvl_cmd),
                ("Movies", self._build_movies_cmd),
                ("Data", self._build_data_cmd),
                ("Fonts", self._build_font_cmd),
            ]
        )

    def _start_pipeline(self, steps: list[tuple[str, Callable[[], list[str]]]]) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("Busy", "A task is already running.")
            return

        if not self._ensure_ready():
            return

        resolved_steps = [(name, builder()) for name, builder in steps]
        for _, cmd in resolved_steps:
            if not self._validate_cmd(cmd):
                return

        self._save_config()
        self.stop_event.clear()
        self._set_running(True, "Running")
        self.worker = threading.Thread(target=self._worker_run_steps, args=(resolved_steps,), daemon=True)
        self.worker.start()

    def _worker_run_steps(self, steps: list[tuple[str, list[str]]]) -> None:
        ok = True
        for name, cmd in steps:
            if self.stop_event.is_set():
                ok = False
                break
            self._log(f"\n=== {name} ===")
            self._log("$ " + shlex.join(cmd))
            code = self._run_subprocess(cmd)
            if code != 0:
                self._log(f"[{name}] FAILED (exit={code})")
                ok = False
                break
            self._log(f"[{name}] COMPLETE")

        if self.stop_event.is_set():
            self._log("Stopped.")
            self.root.after(0, lambda: self._set_running(False, "Stopped"))
            return

        if ok:
            self._log("All requested tasks completed.")
            self.root.after(0, lambda: self._set_running(False, "Complete"))
        else:
            self.root.after(0, lambda: self._set_running(False, "Failed"))

    def _run_subprocess(self, cmd: list[str]) -> int:
        run_cwd = self.repo_root if self.repo_root.exists() else Path.cwd()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(run_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as exc:
            self._log(f"Launch failed: {exc}")
            return 1

        self.current_proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            self._log(line.rstrip("\n"))
            if self.stop_event.is_set() and proc.poll() is None:
                proc.terminate()

        if self.stop_event.is_set() and proc.poll() is None:
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

        code = proc.wait()
        self.current_proc = None
        return code

    def stop_running(self) -> None:
        self.stop_event.set()
        proc = self.current_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--run-extractor":
        return run_embedded_extractor(sys.argv[2], sys.argv[3:])

    root = tk.Tk()
    BZPSPGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
