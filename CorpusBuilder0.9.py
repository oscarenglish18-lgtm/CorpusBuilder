import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import random
import secrets
import base64
import re
import time
import json, csv, hashlib, datetime
from typing import List, Tuple, Dict, Optional

# ---------------------------------------------
# Setup
# ---------------------------------------------
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

DATA_FOLDER = BASE_DIR / "data"
SNAPSHOT_ROOT = DATA_FOLDER / "snapshots"

# Regexes (more tolerant)
GREEK_RANGE_RE = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")
DATE_RE = re.compile(r"dating:\s*(-?\d+)\s+to\s+(-?\d+)", re.I)
EDCS_RE = re.compile(r"EDCS-ID\s*:\s*([A-Za-z0-9\-]+)", re.I)  # tolerant spacing/case
ID_LINE_RE = re.compile(r"^\s*EDCS-ID\s*:\s*([A-Za-z0-9\-]+)\s*$", re.I)
# New EDCS export format: bare ID on its own line e.g. "EDCS-34600208"
EDCS_BARE_RE = re.compile(r"^\s*(EDCS-\d+)\s*$", re.I)

# ---------------------------------------------
# Dataset code system (scope in seed token)
# ---------------------------------------------
# User-facing token format:
#   <CODE>-<SEED>:<N>
# Examples:
#   R-ABCD...:500     (Rome)
#   MA-ABCD...:300    (Mactaris)
#   ALL-ABCD...:1000  (all datasets)

# CODE aliases used to resolve .txt filenames in the local data/ folder.
# Filenames may differ between machines; we match by normalized stem.
_DATASET_CODE_ALIASES = {
    "A":  ["ammaedra", "ammaedara"],
    "BR": ["bulla_regio", "bullaregio", "bulla-regio", "bulla regio"],
    "C":  ["carthage"],
    "H":  ["hadrumetum"],
    "L":  ["lambaesis"],
    "MA": ["mactaris"],
    "MU": ["mustis"],
    "UM": ["uchi_maius", "uchimaius", "uchi-maius", "uchi maius"],
    "R":  ["rome"],
    "S":  ["sufetla", "sbeitla"],
    "TB": ["thibursicum_bure", "thibursicumbure", "thibursicum bure", "thibursicum-bure"],
    "T":  ["thugga", "dougga"],
    "ALL": ["*"],
}

def _norm_stem(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").strip().lower())

def infer_dataset_code_from_filename(filename: str) -> str:
    """Infer a dataset CODE from a dataset filename. Returns '' if unknown."""
    if not filename:
        return ""
    stem = _norm_stem(Path(filename).stem)
    for code, aliases in _DATASET_CODE_ALIASES.items():
        if code == "ALL":
            continue
        for a in aliases:
            if stem == _norm_stem(a):
                return code
    return ""

def resolve_dataset_code_to_files(code: str, all_files: List[Path]) -> List[Path]:
    """Resolve dataset CODE to dataset files present locally."""
    code = (code or "").strip().upper()
    if not code:
        return []
    if code == "ALL":
        return list(all_files)

    aliases = _DATASET_CODE_ALIASES.get(code)
    if not aliases:
        raise ValueError(f"Unknown dataset code: {code}")

    matches: List[Path] = []
    for p in all_files:
        stem = _norm_stem(p.stem)
        for a in aliases:
            if stem == _norm_stem(a):
                matches.append(p)
                break

    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        raise ValueError(f"Dataset code '{code}' matches multiple files: {names}")
    return matches
def normalize_edcs_id(id_or_header: str) -> str:
    """Return bare EDCS ID (no 'EDCS-ID:' prefix) and make filename-safe."""
    m = EDCS_RE.search(id_or_header) or ID_LINE_RE.match(id_or_header)
    raw = (m.group(1) if m else id_or_header).strip()
    return re.sub(r"[^A-Za-z0-9\-_]", "_", raw)


# ---------------------------------------------
# Seed + deterministic selection utilities
# ---------------------------------------------
# Default: 96-bit seeds encoded in Base32 (20 chars, no padding)
_SEED_NBYTES = 12

_BASE32_RE = re.compile(r"^[A-Z2-7]+$")

def _base32_nopad(b: bytes) -> str:
    # Standard base32, uppercase, strip '=' padding
    return base64.b32encode(b).decode("ascii").rstrip("=")

def _group_seed(s: str, group: int = 4) -> str:
    s = s.strip().upper().replace("-", "").replace(" ", "")
    return "-".join(s[i:i+group] for i in range(0, len(s), group))

def generate_seed() -> str:
    """Generate a new cryptographically strong seed token (canonical, no hyphens)."""
    return _base32_nopad(secrets.token_bytes(_SEED_NBYTES))

def canonicalize_seed(seed_text: str) -> str:
    """Return canonical seed (no hyphens/spaces, uppercase). Accepts base32 tokens or decimal integers."""
    s = (seed_text or "").strip().upper().replace("-", "").replace(" ", "")
    if not s:
        return ""
    if s.isdigit():
        # Keep as-is (canonical decimal). This preserves older numeric seeds.
        return s
    if not _BASE32_RE.match(s):
        raise ValueError("Key must be decimal digits or Base32 (A-Z, 2-7), optionally with hyphens.")
    return s

def display_seed(seed_canon: str) -> str:
    """User-facing seed formatting (grouped)"""
    if not seed_canon:
        return ""
    if seed_canon.isdigit():
        return seed_canon
    return _group_seed(seed_canon, 4)


def _canonical_params_suffix(
    date_enabled: bool,
    start_year: Optional[int],
    end_year: Optional[int],
    greek_mode: str,
    exclude_fragments: bool,
) -> str:
    """Return canonical params suffix for tokens/snapshots.

    Format (each part separated by '|'):
      ND               (no dating)
      D<start>_<end>   (dating enabled)
      GI/GE/GO         (Greek Include/Exclude/Greek Only)
      F0/F1            (Exclude Fragments off/on)

    Examples:
      ND|GI|F0
      D-200_300|GE|F1
    """
    gmap = {"Include": "GI", "Exclude": "GE", "Greek Only": "GO"}
    greek_code = gmap.get((greek_mode or "").strip(), "GI")
    frag_code = "F1" if exclude_fragments else "F0"
    if date_enabled and start_year is not None and end_year is not None:
        date_code = f"D{int(start_year)}_{int(end_year)}"
    else:
        date_code = "ND"
    return "|".join([date_code, greek_code, frag_code])


def _parse_params_parts(parts: List[str]) -> Dict[str, object]:
    """Parse param parts after N. Returns dict with canonical values."""
    out: Dict[str, object] = {
        "date_enabled": None,
        "start_year": None,
        "end_year": None,
        "greek_mode": None,
        "exclude_fragments": None,
    }

    for raw in parts:
        p = (raw or "").strip().upper()
        if not p:
            continue

        # Dating
        if p == "ND":
            out["date_enabled"] = False
            out["start_year"] = None
            out["end_year"] = None
            continue
        if p.startswith("D") and "_" in p[1:]:
            try:
                a, b = p[1:].split("_", 1)
                sy = int(a)
                ey = int(b)
            except Exception:
                raise ValueError("Invalid dating parameter. Use ND or D<start>_<end> (e.g., D-200_300).")
            if sy > ey:
                raise ValueError("Invalid dating range in token: start year is greater than end year.")
            out["date_enabled"] = True
            out["start_year"] = sy
            out["end_year"] = ey
            continue

        # Greek
        if p in ("GI", "GE", "GO"):
            out["greek_mode"] = {"GI": "Include", "GE": "Exclude", "GO": "Greek Only"}[p]
            continue

        # Fragments
        if p in ("F0", "F1"):
            out["exclude_fragments"] = (p == "F1")
            continue

        raise ValueError(f"Unrecognized token parameter: '{raw}'.")

    return out


def parse_seed_token(seed_text: str) -> Tuple[str, int, str, Dict[str, object]]:
    """
    Parse a reproduction token.

    Accepted forms:
        SEED
        SEED:N
        CODE-SEED:N
        CODE-SEED:N|PARAMS...

    Where:
        CODE is one of: A, BR, C, H, L, MA, MU, R, S, TB, T, ALL
        SEED is the Base32/decimal seed (hyphens/spaces allowed)
        N is the number of inscriptions

    PARAMS are optional and appear after N, separated by '|'.

    Supported PARAMS:
        ND               (no dating)
        D<start>_<end>   (dating enabled; e.g., D-200_300)
        GI/GE/GO         (Greek Include/Exclude/Greek Only)
        F0/F1            (Exclude Fragments off/on)

    Returns:
        (seed_canon, n_from_token, dataset_code, params_dict)
        - n_from_token is 0 if not provided
        - dataset_code is '' if not provided
        - params_dict fields default to None if not present
    """
    if not seed_text:
        return "", 0, "", {"date_enabled": None, "start_year": None, "end_year": None, "greek_mode": None, "exclude_fragments": None}

    raw = (seed_text or "").strip().upper()

    dataset_code = ""
    rest = raw

    # Split off optional params tail after N
    params_dict = {"date_enabled": None, "start_year": None, "end_year": None, "greek_mode": None, "exclude_fragments": None}
    if "|" in rest:
        rest, tail = rest.split("|", 1)
        tail_parts = [x for x in tail.split("|") if x.strip()]
        params_dict = _parse_params_parts(tail_parts)

    # Dataset code prefix is parsed from the portion *before* any params suffix.
    if "-" in rest:
        maybe_code, maybe_rest = rest.split("-", 1)
        maybe_code = maybe_code.strip()
        if maybe_code in _DATASET_CODE_ALIASES and maybe_code != "*":
            dataset_code = maybe_code
            rest = maybe_rest.strip()

    if ":" in rest:
        seed_part, n_part = rest.split(":", 1)
        seed_part = seed_part.strip()
        n_part = n_part.strip()
        if not n_part:
            raise ValueError("Seed token has ':' but no number after it.")
        try:
            n_val = int(n_part)
        except ValueError:
            raise ValueError("Invalid number after ':' in seed token.")
    else:
        seed_part = rest.strip()
        n_val = 0

    seed_canon = canonicalize_seed(seed_part)
    return seed_canon, n_val, dataset_code, params_dict


def dataset_fingerprint(files: List[Path]) -> str:
    """Stable fingerprint for a set of source files."""
    # Hash of (name + sha256(file)) pairs sorted by name.
    pairs = []
    for p in sorted([Path(x) for x in files], key=lambda x: x.name.lower()):
        try:
            pairs.append((p.name, _sha256_file(p)))
        except Exception:
            pairs.append((p.name, "ERROR"))
    h = hashlib.sha256()
    for name, sh in pairs:
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(sh.encode("ascii", errors="ignore"))
        h.update(b"\n")
    return h.hexdigest()

def deterministic_select(
    inscriptions: List[Tuple[str, str]],  # [(bare_edcs_id, cleaned_text), ...]
    n: int,
    seed_canon: str,
    ds_fp: str,
) -> List[Tuple[str, str]]:
    """
    Deterministically select and order n inscriptions using hash-ranking:
      score(id) = SHA256(seed | dataset_fingerprint | id)
    """
    if n <= 0:
        return []
    def score(eid: str) -> bytes:
        msg = f"{seed_canon}|{ds_fp}|{eid}".encode("utf-8")
        return hashlib.sha256(msg).digest()
    ranked = sorted(inscriptions, key=lambda it: (score(it[0]), it[0]))
    return ranked[:n]


# ---------------------------------------------
# Snapshot utilities (single corpus file)
# ---------------------------------------------
def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()

def write_seed_snapshot(
    seed: str,
    selection_ordered: List[Tuple[str, str]],  # [(bare_edcs_id, text), ...] in final order
    corpus_text: str,                          # NEW: full combined text of the corpus
    source_files: List[Path],
    mode: str,
    filters: dict,
    app_name: str = "EDCS Corpus Builder",
    app_version: str = "1.2.0",
) -> Path:
    """
    Writes a frozen, seed-named snapshot under:
        data/snapshots/seed-<seed>/
          - selection.csv        (rank, edcs_id)
          - corpus.txt           (entire corpus in one file)
          - manifest.json
          - README.txt
    Returns the run directory path.
    """
    run_dir = SNAPSHOT_ROOT / f"seed-{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 1) selection.csv (ordered, minimalist)
    with (run_dir / "selection.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "edcs_id"])
        for i, (eid, _) in enumerate(selection_ordered, start=1):
            bare = normalize_edcs_id(eid)
            w.writerow([i, bare])

    # 2) corpus.txt (single combined file)
    (run_dir / "corpus.txt").write_text(corpus_text, encoding="utf-8", newline="\n")

    # 3) manifest.json (provenance)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    src_meta = []
    for s in source_files:
        try:
            s = Path(s)
            src_meta.append({
                "path": str(s),
                "sha256": _sha256_file(s),
                "size_bytes": s.stat().st_size,
            })
        except Exception as e:
            src_meta.append({"path": str(s), "error": repr(e)})

    manifest = {
        "seed": str(seed),
        "timestamp": ts,
        "mode": mode,
        "filters": filters,
        "n_selected": len(selection_ordered),
        "app": {"name": app_name, "version": app_version},
        "sources": src_meta,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")

    # 4) README
    (run_dir / "README.txt").write_text(
        "This folder is a frozen snapshot of the corpus for the given seed.\n"
        "Re-generate by choosing the same seed; contents are independent of other parameters.\n"
        "selection.csv indexes EDCS-IDs; corpus.txt contains the full text.\n",
        "utf-8"
    )

    return run_dir

def _parse_corpus_blocks(text: str) -> List[Tuple[str, str]]:
    """
    Parse a combined corpus into [(bare_id, text), ...] by splitting on '****\\nEDCS-ID: <id>\\n'.
    """
    parts = text.split("****\n")
    out: List[Tuple[str, str]] = []
    for part in parts:
        if not part.strip():
            continue
        lines = part.splitlines()
        if not lines:
            continue
        m = ID_LINE_RE.match(lines[0])
        if not m:
            # tolerate stray headerless chunks
            continue
        bare = normalize_edcs_id(m.group(1))
        body = "\n".join(lines[1:]).rstrip()
        out.append((bare, body))
    return out

def read_seed_snapshot(seed: str):
    """
    Returns ordered [(bare_edcs_id, text), ...] if a snapshot for this seed exists.
    Prefers corpus.txt; falls back to older per-file snapshots if needed.
    """
    run_dir = SNAPSHOT_ROOT / f"seed-{seed}"
    if not run_dir.exists():
        return None

    sel = run_dir / "selection.csv"
    corpus = run_dir / "corpus.txt"

    # Prefer the new single-file corpus
    if sel.exists() and corpus.exists():
        try:
            # parse corpus into blocks
            blocks = _parse_corpus_blocks(corpus.read_text(encoding="utf-8"))
            if not blocks:
                print(f"DEBUG: No blocks parsed from corpus.txt for seed {seed}")
                return None
            # if selection.csv exists, use it to order/filter by the recorded IDs
            order_ids = []
            with sel.open("r", encoding="utf-8", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    eid = (row.get("edcs_id") or "").strip()
                    if eid:
                        order_ids.append(eid)
            if order_ids:
                # index parsed blocks by id, then return in selection.csv order
                by_id = {eid: txt for (eid, txt) in blocks}
                out = [(eid, by_id[eid]) for eid in order_ids if eid in by_id]
                if not out:
                    print(f"DEBUG: No ID matches for seed {seed}")
                    print(f"  selection.csv IDs (first 3): {order_ids[:3]}")
                    print(f"  corpus.txt IDs (first 3): {list(by_id.keys())[:3]}")
                if out:
                    return out
            # fallback: return blocks in parsed order
            return blocks
        except Exception as e:
            print(f"DEBUG: Exception reading snapshot for seed {seed}: {e}")
            import traceback
            traceback.print_exc()
            return None

    # Back-compat: read old format with per-inscription files
    sel_old = run_dir / "selection.csv"
    files_dir = None
    for candidate in run_dir.iterdir():
        if candidate.is_dir() and candidate.name.startswith(f"seed-{seed}-files"):
            files_dir = candidate
            break
    if sel_old.exists() and files_dir is not None:
        rows = []
        try:
            with sel_old.open("r", encoding="utf-8", newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    eid = (row.get("edcs_id") or "").strip()
                    rel = (row.get("filename") or "").strip()
                    if not eid or not rel:
                        continue
                    p = (run_dir / rel).resolve()
                    if not p.exists():
                        continue
                    text = p.read_text(encoding="utf-8")
                    rows.append((eid, text))
        except Exception:
            return None
        return rows or None

    return None

# ---------------------------------------------
# App
# ---------------------------------------------
class CorpusBuilderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EDCS Corpus Builder")
        self.geometry("900x780")
        self.configure(bg="#f0f0f0")
        
        # Ensure clipboard persists after app closes
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        header = tk.Frame(self, bg="#dcdcdc", pady=10)
        header.pack(fill="x")
        tk.Label(header, text="EDCS Corpus Builder", font=("Courier", 20, "bold"), bg="#dcdcdc").pack()

        options_row = tk.Frame(self, bg="#f0f0f0")
        options_row.pack(pady=5)
        tk.Label(options_row, text="Key (optional):", bg="#f0f0f0").pack(side="left", padx=5)
        self.seed_entry = tk.Entry(options_row, width=32)
        self.seed_entry.pack(side="left", padx=5)
        self._add_context_menu(self.seed_entry)

        tk.Button(options_row, text="Copy", width=6, command=self.copy_seed_to_clipboard).pack(side="left", padx=3)

        self.exclude_short = tk.BooleanVar(value=False)
        tk.Checkbutton(
            options_row,
            text="Exclude Fragments",
            variable=self.exclude_short,
            bg="#f0f0f0"
        ).pack(side="left", padx=10)

        form_row = tk.Frame(self, bg="#f0f0f0")
        form_row.pack(pady=10)
        tk.Label(form_row, text="Dataset:", bg="#f0f0f0").pack(side="left", padx=5)
        self.dataset_var = tk.StringVar()
        self.dataset_dropdown = ttk.Combobox(form_row, textvariable=self.dataset_var, width=40, state="readonly")
        self.refresh_dataset_list()
        self.dataset_dropdown.pack(side="left", padx=5)

        tk.Label(form_row, text="Number:", bg="#f0f0f0").pack(side="left", padx=5)
        self.num_entry = tk.Entry(form_row, width=10)
        self.num_entry.pack(side="left", padx=5)
        self._add_context_menu(self.num_entry)

        tk.Label(form_row, text="Greek Inscriptions:", bg="#f0f0f0").pack(side="left", padx=5)
        self.greek_option = tk.StringVar(value="Include")
        self.greek_dropdown = ttk.Combobox(
            form_row,
            textvariable=self.greek_option,
            values=["Include", "Exclude", "Greek Only"],
            width=15,
            state="readonly",
        )
        self.greek_dropdown.pack(side="left", padx=5)

        date_row = tk.Frame(self, bg="#f0f0f0")
        date_row.pack(pady=(5, 0))
        self.date_filter_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(
            date_row,
            text="Enable Date Filter",
            variable=self.date_filter_enabled,
            command=self.toggle_date_widgets,
            bg="#f0f0f0",
        ).pack(side="left", padx=10)

        tk.Label(date_row, text="Start Year:", bg="#f0f0f0").pack(side="left", padx=5)
        self.start_year_spin = ttk.Spinbox(date_row, from_=-600, to=1500, width=8, state="disabled")
        self.start_year_spin.set(-600)
        self.start_year_spin.pack(side="left")

        tk.Label(date_row, text="End Year:", bg="#f0f0f0").pack(side="left", padx=5)
        self.end_year_spin = ttk.Spinbox(date_row, from_=-600, to=1500, width=8, state="disabled")
        self.end_year_spin.set(1500)
        self.end_year_spin.pack(side="left")

        save_row = tk.Frame(self, bg="#f0f0f0")
        save_row.pack(pady=5)
        tk.Label(save_row, text="Save to:", bg="#f0f0f0").pack(side="left", padx=5)
        self.save_entry = tk.Entry(save_row, width=60)
        self.save_entry.pack(side="left", padx=5)
        self._add_context_menu(self.save_entry)
        tk.Button(save_row, text="Browse", command=self.select_save_location).pack(side="left", padx=5)

        tk.Button(self, text="Build Corpus", command=self.build_corpus, bg="#d9ead3").pack(pady=10)

        self.info_box = tk.Text(self, height=25, wrap="word", bg="white", fg="black")
        self.info_box.pack(expand=True, fill="both", padx=10, pady=10)
        self._add_text_context_menu(self.info_box)

        self.status = tk.Label(self, text="Ready", bg="#ddd", anchor="w")
        self.status.pack(side="bottom", fill="x")

    def toggle_date_widgets(self):
        state = "normal" if self.date_filter_enabled.get() else "disabled"
        self.start_year_spin.configure(state=state)
        self.end_year_spin.configure(state=state)

    def _on_closing(self):
        """Handle window close event - persist clipboard and clean exit."""
        try:
            # Try to persist current clipboard content
            # This forces the OS to take ownership of clipboard data
            clip_data = self.clipboard_get()
            if clip_data:
                # Clear and re-append to force OS-level persistence
                self.clipboard_clear()
                self.clipboard_append(clip_data)
                self.update()  # Process all pending events
        except Exception:
            pass
        finally:
            self.destroy()

    
    def copy_seed_to_clipboard(self):
        """Copy the current seed/token to the OS clipboard (persistently)."""
        text = self.seed_entry.get().strip()
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update()  # critical: forces OS-level clipboard ownership
            try:
                self.status.config(text="Key copied to clipboard")
                self.after(1500, lambda: self.status.config(text="Ready"))
            except Exception:
                pass
        except Exception:
            pass

    def refresh_dataset_list(self):
        files = list(DATA_FOLDER.glob("*.txt"))
        self.dataset_dropdown["values"] = [f.name for f in files]
        if files:
            self.dataset_var.set(files[0].name)

    def select_save_location(self):
        path = filedialog.asksaveasfilename(defaultextension=".txt", title="Save Cleaned File As")
        if path:
            self.save_entry.delete(0, tk.END)
            self.save_entry.insert(0, path)

    def _add_context_menu(self, widget):
        """Add right-click context menu to Entry widget.

        NOTE: On many systems a right-click does not move keyboard focus.
        We therefore explicitly focus the widget and implement paste via
        clipboard_get() to ensure it works reliably.
        """
        context_menu = tk.Menu(widget, tearoff=0)

        def _focus_at_event(event=None):
            try:
                widget.focus_set()
            except Exception:
                pass
            if event is not None:
                # Move insertion cursor where the user clicked (Entry supports @x)
                try:
                    widget.icursor(widget.index(f"@{event.x}"))
                except Exception:
                    pass

        def do_cut():
            _focus_at_event()
            widget.event_generate("<<Cut>>")

        def do_copy():
            _focus_at_event()
            widget.event_generate("<<Copy>>")

        def do_paste():
            _focus_at_event()
            try:
                txt = widget.clipboard_get()
            except tk.TclError:
                return
            # Replace current selection if present
            try:
                if widget.selection_present():
                    widget.delete(widget.index("sel.first"), widget.index("sel.last"))
            except Exception:
                pass
            widget.insert(widget.index(tk.INSERT), txt)

        def do_select_all():
            _focus_at_event()
            try:
                widget.select_range(0, tk.END)
                widget.icursor(tk.END)
            except Exception:
                # ttk.Entry uses selection_range in some Tk versions
                try:
                    widget.selection_range(0, tk.END)
                    widget.icursor(tk.END)
                except Exception:
                    pass

        context_menu.add_command(label="Cut", command=do_cut)
        context_menu.add_command(label="Copy", command=do_copy)
        context_menu.add_command(label="Paste", command=do_paste)
        context_menu.add_separator()
        context_menu.add_command(label="Select All", command=do_select_all)

        def show_context_menu(event):
            _focus_at_event(event)
            try:
                context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                context_menu.grab_release()

        widget.bind("<Button-3>", show_context_menu)  # Right-click
        # Also bind for Mac (Button-2 or Control-Button-1)
        if self.tk.call('tk', 'windowingsystem') == 'aqua':
            widget.bind("<Button-2>", show_context_menu)
            widget.bind("<Control-Button-1>", show_context_menu)

    def _add_text_context_menu(self, text_widget):
        """Add right-click context menu to Text widget."""
        context_menu = tk.Menu(text_widget, tearoff=0)
        context_menu.add_command(label="Copy", command=lambda: text_widget.event_generate("<<Copy>>"))
        context_menu.add_separator()
        context_menu.add_command(label="Select All", command=lambda: text_widget.tag_add(tk.SEL, "1.0", tk.END))
        
        def show_context_menu(event):
            try:
                context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                context_menu.grab_release()
        
        text_widget.bind("<Button-3>", show_context_menu)  # Right-click
        # Also bind for Mac
        if self.tk.call('tk', 'windowingsystem') == 'aqua':
            text_widget.bind("<Button-2>", show_context_menu)
            text_widget.bind("<Control-Button-1>", show_context_menu)

    @staticmethod
    def _split_into_blocks(lines: List[str]) -> List[Tuple[str, List[str]]]:
        """
        Robust split into (edcs_id_line, block_lines) using EDCS_RE.
        Preserves any text to the left of the ID line within the block.
        """
        blocks: List[Tuple[str, List[str]]] = []
        current_lines: List[str] = []
        current_id: str = None

        for raw in lines:
            line = raw.rstrip("\n")
            m = EDCS_RE.search(line)
            m_bare = EDCS_BARE_RE.match(line) if not m else None
            if m:
                # Old format: "EDCS-ID: EDCS-XXXXXXXX" anywhere on the line
                if current_id and current_lines:
                    blocks.append((current_id, current_lines))
                    current_lines = []
                current_id = f"EDCS-ID: {m.group(1)}"
                left = line[:m.start()].strip()
                if left:
                    current_lines.append(left)
            elif m_bare:
                # New format: bare "EDCS-XXXXXXXX" on its own line
                if current_id and current_lines:
                    blocks.append((current_id, current_lines))
                    current_lines = []
                current_id = f"EDCS-ID: {m_bare.group(1)}"
            else:
                current_lines.append(line.strip())

        if current_id and current_lines:
            blocks.append((current_id, current_lines))

        return blocks

    def build_corpus(self):
        try:
            filename = self.dataset_var.get()
            greek_filter = self.greek_option.get()

            # UI parameters (may be overridden by a token's encoded params)
            ui_date_enabled = bool(self.date_filter_enabled.get())
            ui_start_year = int(self.start_year_spin.get())
            ui_end_year = int(self.end_year_spin.get())
            ui_exclude_fragments = bool(self.exclude_short.get())

            # Parse number if provided (will validate later based on whether we have a seed)
            num_text = self.num_entry.get().strip()
            n = None
            if num_text:
                try:
                    n = int(num_text)
                except ValueError:
                    messagebox.showerror("Invalid Number", "Please enter a valid number of inscriptions.")
                    return

            seed_text_raw = self.seed_entry.get().strip()
            try:
                seed_canon, n_from_seed, ds_code_from_seed, token_params = parse_seed_token(seed_text_raw)
            except ValueError as ve:
                messagebox.showwarning("Invalid Key", str(ve))
                return

            seed_provided = bool(seed_canon)
            # If the seed token contains N and the Number box is empty, use the token's N
            if n is None and n_from_seed:
                n = n_from_seed
            # Determine dataset scope:
            # - If CODE provided in token: that scope overrides the dropdown.
            # - Else: scope comes from the dropdown selection (if its filename maps to a code).
            dataset_code = (ds_code_from_seed or "").strip().upper()
            if not dataset_code:
                dataset_code = infer_dataset_code_from_filename(filename)  # may be ''

            # Effective parameters: token params (if provided) override UI
            greek_filter_eff = token_params.get("greek_mode") if token_params.get("greek_mode") is not None else greek_filter

            if token_params.get("date_enabled") is None:
                date_enabled_eff = ui_date_enabled
                start_year_eff = ui_start_year
                end_year_eff = ui_end_year
            else:
                date_enabled_eff = bool(token_params.get("date_enabled"))
                start_year_eff = token_params.get("start_year") if token_params.get("start_year") is not None else ui_start_year
                end_year_eff = token_params.get("end_year") if token_params.get("end_year") is not None else ui_end_year

            exclude_fragments_eff = token_params.get("exclude_fragments") if token_params.get("exclude_fragments") is not None else ui_exclude_fragments

            if date_enabled_eff and start_year_eff is not None and end_year_eff is not None and int(start_year_eff) > int(end_year_eff):
                messagebox.showerror("Invalid Date Range", "Start year cannot be greater than end year.")
                return

            # If no seed provided, mint a new cryptographically strong seed for this run.
            # Only mint once we know N (either from the Number box or from a SEED:N token).
            if not seed_canon and n is not None:
                seed_canon = generate_seed()
                # show grouped form for readability + include :N and params for reproducibility
                token = display_seed(seed_canon)
                code_prefix = infer_dataset_code_from_filename(filename)
                if code_prefix:
                    token = f"{code_prefix}-{token}"
                token = f"{token}:{n}" if n else token
                params_suffix = _canonical_params_suffix(
                    date_enabled=date_enabled_eff,
                    start_year=int(start_year_eff) if date_enabled_eff else None,
                    end_year=int(end_year_eff) if date_enabled_eff else None,
                    greek_mode=greek_filter_eff,
                    exclude_fragments=bool(exclude_fragments_eff),
                )
                token = f"{token}|{params_suffix}"
                self.seed_entry.delete(0, tk.END)
                self.seed_entry.insert(0, token)
                seed_provided = True

            # If user supplied a seed without ':N' but we have N, normalize the entry to SEED:N for copy/paste.
            if seed_canon and n is not None and ":" not in seed_text_raw:
                token2 = display_seed(seed_canon)
                code_prefix2 = dataset_code or infer_dataset_code_from_filename(filename)
                if code_prefix2:
                    token2 = f"{code_prefix2}-{token2}"
                token2 = f"{token2}:{n}" if n else token2
                params_suffix2 = _canonical_params_suffix(
                    date_enabled=date_enabled_eff,
                    start_year=int(start_year_eff) if date_enabled_eff else None,
                    end_year=int(end_year_eff) if date_enabled_eff else None,
                    greek_mode=greek_filter_eff,
                    exclude_fragments=bool(exclude_fragments_eff),
                )
                token2 = f"{token2}|{params_suffix2}"
                self.seed_entry.delete(0, tk.END)
                self.seed_entry.insert(0, token2)

            # If user supplied a legacy token with ':N' but no params, append params for reproducibility.
            if seed_canon and n is not None and ":" in seed_text_raw and "|" not in seed_text_raw:
                token3 = display_seed(seed_canon)
                code_prefix3 = dataset_code or infer_dataset_code_from_filename(filename)
                if code_prefix3:
                    token3 = f"{code_prefix3}-{token3}"
                token3 = f"{token3}:{n}" if n else token3
                params_suffix3 = _canonical_params_suffix(
                    date_enabled=date_enabled_eff,
                    start_year=int(start_year_eff) if date_enabled_eff else None,
                    end_year=int(end_year_eff) if date_enabled_eff else None,
                    greek_mode=greek_filter_eff,
                    exclude_fragments=bool(exclude_fragments_eff),
                )
                token3 = f"{token3}|{params_suffix3}"
                self.seed_entry.delete(0, tk.END)
                self.seed_entry.insert(0, token3)


            save_path = self.save_entry.get().strip()
            if not save_path:
                messagebox.showerror("Missing Path", "Please choose where to save the output.")
                return

            # --- SNAPSHOT SHORT-CIRCUIT: reuse if exists ---
            # Snapshot key must include *all* selection-defining inputs (dataset scope, N, and params)
            params_for_key = _canonical_params_suffix(
                date_enabled=date_enabled_eff,
                start_year=int(start_year_eff) if date_enabled_eff else None,
                end_year=int(end_year_eff) if date_enabled_eff else None,
                greek_mode=greek_filter_eff,
                exclude_fragments=bool(exclude_fragments_eff),
            )
            params_safe = re.sub(r"[^A-Z0-9_\-]+", "-", params_for_key.upper())
            # Always include a city identifier in the key.
            # Use the dataset CODE if resolved, otherwise fall back to the
            # normalised filename stem (e.g. "thugga" from "thugga.txt").
            city_label = dataset_code or re.sub(r"[^A-Z0-9]+", "-", Path(filename).stem.upper()).strip("-")
            snapshot_key = f"{city_label}-{seed_canon}-N{n}-{params_safe}"
            existing = read_seed_snapshot(snapshot_key)
            if existing:
                # Snapshot exists - seed is SUPERIOR, ignore number field
                n = len(existing)
                
                subset = existing  # Use entire snapshot
                rendered_blocks = [f"****\nEDCS-ID: {eid}\n{text}\n" for (eid, text) in subset]
                Path(save_path).write_text("\n".join(rendered_blocks), encoding="utf-8")
                self.info_box.delete("1.0", tk.END)
                self.info_box.insert(tk.END, "\n".join(rendered_blocks))
                run_dir = SNAPSHOT_ROOT / f"seed-{snapshot_key}"
                self.status.config(text=f"Reused snapshot for Token {self.seed_entry.get().strip()} ({n} inscriptions)  |  Saved: {save_path}  |  Snapshot: {run_dir}")
                return
            # -------------------------------------------------------------------------------

            # No snapshot exists - need to generate new corpus
            if n is None:
                messagebox.showerror(
                    "Missing Number",
                    "Number of inscriptions not provided. Enter a number, or use a CODE-SEED:N token (e.g., R-ABCD...:500) or enter a number."
                )
                return

            if not filename:
                messagebox.showerror("No Dataset", "Please select a dataset.")
                return


            # Determine source files to use
            all_files = sorted(list(DATA_FOLDER.glob("*.txt")), key=lambda p: p.name.lower())
            if not all_files:
                messagebox.showerror("No Data", f"No .txt datasets found in: {DATA_FOLDER}")
                return
            # Scope selection:
            # - If a token CODE is provided:
            #     * ALL -> all files
            #     * otherwise -> the matching dataset file
            # - If no token CODE is provided -> use the dataset dropdown selection
            use_all = False
            if dataset_code == "ALL":
                use_all = True
                source_files = all_files
            elif dataset_code:
                try:
                    resolved = resolve_dataset_code_to_files(dataset_code, all_files)
                except ValueError as ve:
                    messagebox.showerror("Dataset Code Error", str(ve))
                    return
                if not resolved:
                    messagebox.showerror("Dataset Code Not Found", f"No dataset file found for code: {dataset_code}")
                    return
                source_files = resolved
            else:
                source_files = [DATA_FOLDER / filename]
            for p in source_files:
                if not p.exists():
                    messagebox.showerror("Missing File", f"Dataset not found: {p}")
                    return

            # Load and split blocks from all chosen source files
            blocks_by_file = []  # [(path, blocks)]
            for p in source_files:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                blocks_by_file.append((p, self._split_into_blocks(lines)))

            # Filters: ALWAYS applied. Seed is used only for deterministic selection.
            if date_enabled_eff:
                filter_start = int(start_year_eff)
                filter_end = int(end_year_eff)
            else:
                filter_start = filter_end = None

            inscriptions: List[Tuple[str, str]] = []  # (bare_edcs_id, cleaned_text)
            seen_ids = set()

            for p, blocks in blocks_by_file:
                for edcs_id_line, raw_lines in blocks:
                    original_text = "\n".join(raw_lines)
                    contains_greek = bool(GREEK_RANGE_RE.search(original_text))
                    if greek_filter_eff == "Exclude" and contains_greek:
                        continue
                    if greek_filter_eff == "Greek Only" and not contains_greek:
                        continue

                    if filter_start is not None:
                        m = DATE_RE.search(original_text)
                        if not m:
                            continue
                        start, end = map(int, m.groups())
                        if end < filter_start or start > filter_end:
                            continue

                    cleaned = self.clean_inscription_lines(raw_lines, greek_filter_eff == "Greek Only")
                    cleaned_chars = cleaned.replace('\n', '').strip()
                    bare_id = normalize_edcs_id(edcs_id_line)
                    if bare_id in seen_ids:
                        continue
                    if cleaned and (not exclude_fragments_eff or len(cleaned_chars) > 3):
                        seen_ids.add(bare_id)
                        inscriptions.append((bare_id, cleaned))

            total_found = len(inscriptions)
            if total_found == 0:
                messagebox.showerror("No Matches", "No inscriptions matched your criteria.")
                return
            if total_found < n:
                messagebox.showerror(
                    "Too Few Matches",
                    f"Only {total_found} inscriptions matched your filters, but you requested {n}."
                )
                return

            # Deterministic selection via hash-ranking (stable across devices/Python versions)
            ds_fp = dataset_fingerprint(source_files)
            subset_pairs = deterministic_select(inscriptions, n, seed_canon, ds_fp)  # [(bare_id, text), ...] in final order

            # Render for file/UI (combined corpus text)
            rendered_blocks = [f"****\nEDCS-ID: {eid}\n{text}\n" for (eid, text) in subset_pairs]
            corpus_text = "\n".join(rendered_blocks)
            Path(save_path).write_text(corpus_text, encoding="utf-8")

            # Build snapshot payload
            selection_ordered = subset_pairs

            filters = {
                "greek": greek_filter_eff,
                "date_filter_enabled": bool(date_enabled_eff),
                "start_year": int(start_year_eff) if date_enabled_eff else None,
                "end_year": int(end_year_eff) if date_enabled_eff else None,
                "exclude_fragments": exclude_fragments_eff,
                "dataset": (dataset_code or self.dataset_var.get()),
                "dataset_fingerprint": ds_fp,
            }
            run_dir = write_seed_snapshot(
                seed=str(snapshot_key),
                selection_ordered=selection_ordered,
                corpus_text=corpus_text,              # NEW: single-file snapshot
                source_files=source_files,
                mode="Normalized (current)",
                filters=filters,
                app_version="1.2.0"
            )

            # UI
            self.info_box.delete("1.0", tk.END)
            self.info_box.insert(tk.END, corpus_text)
            self.status.config(text=f"Saved {n} cleaned inscriptions to: {save_path} (Token: {self.seed_entry.get().strip()})  |  Snapshot: {run_dir}")

        except Exception as exc:
            import traceback
            traceback.print_exc()
            messagebox.showerror("Error", str(exc))

    @staticmethod
    def clean_inscription_lines(lines: List[str], greek_only: bool = False) -> str:
        """
        Normalized cleaner (preserve restorations):
        - drops metadata lines
        - removes editorial additions: (...) and <...>
        - PRESERVES contents of [...] by removing only the brackets
        - U/u -> V, uppercase, light punctuation keep
        """
        cleaned: List[str] = []
        metadata_prefixes = (
            # Old format fields
            "province:", "place:", "findspot:", "author", "editor",
            "status:", "genus:", "comment:", "comments:", "inscriptiones", "publication:",
            # Both formats: "material:" (old) and "material lapis" (new) — match on stem
            "material",
            # New format fields
            "localisation", "evidence", "inscriptions",
        )
        abbreviation_patterns = {
            r"\bD\s+M\s+S\b": "DMS",
            r"\bD\s+M\b": "DM",
            r"\bH\s+S\s+E\b": "HSE",
            r"\bH\s+S\b": "HS",
            r"\bH\s+E\s+S\b": "HES",
            r"\bS\s+T\s+T\s+L\b": "STTL",
            r"\bB\s+M\b": "BM",
            r"\bF\s+F\b": "FF",
            r"\bF\s+C\b": "FC",
            r"\bV\s+S\s+L\s+M\b": "VSLM",
            r"\bP\s+V\s+A\s+N\b": "PVAN",
            r"\bP\s+V\s+A\b": "PVA",
            r"\bV\s+A\b": "VA",
            r"\bA\s+N\b": "AN",
            r"\bC\s+R\b": "CR",
            r"\bC\s+I\b": "CI",
            r"\bC\s+S\b": "CS",
            r"\bC\s+O\b": "CO",
            r"\bD\s+D\b": "DD",
            r"\bD\s+E\b": "DE",
            r"\bD\s+F\b": "DF",
            r"\bD\s+I\b": "DI",
            r"\bD\s+O\b": "DO",
            r"\bD\s+S\b": "DS",
            r"\bE\s+M\b": "EM",
            r"\bE\s+Q\b": "EQ",
            r"\bF\s+A\b": "FA",
            r"\bM\s+F\b": "MF",
            r"\bM\s+L\b": "ML",
            r"\bS\s+T\b": "ST",
        }

        for line in lines:
            original = line.strip()
            if not original:
                continue
            lower = original.lower()
            if lower.startswith("inscription genus") or any(lower.startswith(p) for p in metadata_prefixes):
                continue

            # Editorial handling: remove (), <>, but keep contents of []
            line = re.sub(r"\([^)]*\)", "", original)   # remove ( ... )
            line = re.sub(r"<[^>]*>", "", line)         # remove < ... >
            line = line.replace("[", "").replace("]", "")  # keep restored letters, drop brackets only

            # Normalization (current behavior)
            line = re.sub(r"[uU]", "V", line)
            if greek_only:
                line = re.sub(r"[^A-ZΑ-Ωα-ωΆ-ώ0-9\s\-–:.·]", "", line.upper())
            else:
                line = re.sub(r"[^A-Z0-9\s\-–:.·]", "", line.upper())
            line = re.sub(r"\s{2,}", " ", line).strip()

            for pattern, repl in abbreviation_patterns.items():
                line = re.sub(pattern, repl, line)

            if line:
                cleaned.append(line)

        return "\n".join(cleaned)

# ---------------------------------------------
# Main
# ---------------------------------------------
if __name__ == "__main__":
    app = CorpusBuilderApp()
    app.mainloop()

