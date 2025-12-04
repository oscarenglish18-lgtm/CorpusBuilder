import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import random
import re
import time
import json, csv, hashlib, datetime
from typing import List, Tuple

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

def normalize_edcs_id(id_or_header: str) -> str:
    """Return bare EDCS ID (no 'EDCS-ID:' prefix) and make filename-safe."""
    m = EDCS_RE.search(id_or_header) or ID_LINE_RE.match(id_or_header)
    raw = (m.group(1) if m else id_or_header).strip()
    return re.sub(r"[^A-Za-z0-9\-_]", "_", raw)

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
    app_version: str = "1.1.3",
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
        bare = normalize_edcs_id(m.group(0))
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
                if out:
                    return out
            # fallback: return blocks in parsed order
            return blocks
        except Exception:
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

        header = tk.Frame(self, bg="#dcdcdc", pady=10)
        header.pack(fill="x")
        tk.Label(header, text="EDCS Corpus Builder", font=("Courier", 20, "bold"), bg="#dcdcdc").pack()

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

        options_row = tk.Frame(self, bg="#f0f0f0")
        options_row.pack(pady=5)
        tk.Label(options_row, text="Random Seed (optional):", bg="#f0f0f0").pack(side="left", padx=5)
        self.seed_entry = tk.Entry(options_row, width=20)
        self.seed_entry.pack(side="left", padx=5)

        self.exclude_short = tk.BooleanVar(value=False)
        tk.Checkbutton(
            options_row,
            text="Exclude Fragments",
            variable=self.exclude_short,
            bg="#f0f0f0"
        ).pack(side="left", padx=10)

        save_row = tk.Frame(self, bg="#f0f0f0")
        save_row.pack(pady=5)
        tk.Label(save_row, text="Save to:", bg="#f0f0f0").pack(side="left", padx=5)
        self.save_entry = tk.Entry(save_row, width=60)
        self.save_entry.pack(side="left", padx=5)
        tk.Button(save_row, text="Browse", command=self.select_save_location).pack(side="left", padx=5)

        tk.Button(self, text="Build Corpus", command=self.build_corpus, bg="#d9ead3").pack(pady=10)

        self.info_box = tk.Text(self, height=25, wrap="word", bg="white", fg="black")
        self.info_box.pack(expand=True, fill="both", padx=10, pady=10)

        self.status = tk.Label(self, text="Ready", bg="#ddd", anchor="w")
        self.status.pack(side="bottom", fill="x")

    def toggle_date_widgets(self):
        state = "normal" if self.date_filter_enabled.get() else "disabled"
        self.start_year_spin.configure(state=state)
        self.end_year_spin.configure(state=state)

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
            if m:
                if current_id and current_lines:
                    blocks.append((current_id, current_lines))
                    current_lines = []
                current_id = f"EDCS-ID: {m.group(1)}"
                left = line[:m.start()].strip()
                if left:
                    current_lines.append(left)
            else:
                current_lines.append(line.strip())

        if current_id and current_lines:
            blocks.append((current_id, current_lines))

        return blocks

    def build_corpus(self):
        try:
            filename = self.dataset_var.get()
            greek_filter = self.greek_option.get()

            try:
                n = int(self.num_entry.get())
            except ValueError:
                messagebox.showerror("Invalid Number", "Please enter a valid number of inscriptions.")
                return

            seed_text = self.seed_entry.get().strip()
            if seed_text:
                try:
                    seed_value = int(seed_text)
                except ValueError:
                    messagebox.showwarning("Invalid Seed", "Seed must be a number.")
                    return
            else:
                seed_value = int(time.time() * 1000)
                self.seed_entry.insert(0, str(seed_value))

            save_path = self.save_entry.get().strip()
            if not save_path:
                messagebox.showerror("Missing Path", "Please choose where to save the output.")
                return

            # --- SNAPSHOT SHORT-CIRCUIT: reuse if exists (seed overrides filters/dataset) ---
            existing = read_seed_snapshot(str(seed_value))
            if existing:
                if len(existing) < n:
                    messagebox.showerror(
                        "Snapshot Smaller Than Request",
                        f"Snapshot for seed {seed_value} contains {len(existing)} items but you requested {n}."
                    )
                    return
                subset = existing[:n]  # [(bare_id, text), ...]
                rendered_blocks = [f"****\nEDCS-ID: {eid}\n{text}\n" for (eid, text) in subset]
                Path(save_path).write_text("\n".join(rendered_blocks), encoding="utf-8")
                self.info_box.delete("1.0", tk.END)
                self.info_box.insert(tk.END, "\n".join(rendered_blocks))
                run_dir = SNAPSHOT_ROOT / f"seed-{seed_value}"
                self.status.config(text=f"Reused snapshot for Seed {seed_value}  |  Saved: {save_path}  |  Snapshot: {run_dir}")
                return
            # -------------------------------------------------------------------------------

            if not filename:
                messagebox.showerror("No Dataset", "Please select a dataset.")
                return

            # seed RNG for this run
            random.seed(seed_value)

            dataset_path = DATA_FOLDER / filename
            if not dataset_path.exists():
                messagebox.showerror("Missing File", f"Dataset not found: {dataset_path}")
                return

            lines = dataset_path.read_text(encoding="utf-8").splitlines()
            blocks = self._split_into_blocks(lines)

            # Filters
            if self.date_filter_enabled.get():
                filter_start = int(self.start_year_spin.get())
                filter_end = int(self.end_year_spin.get())
            else:
                filter_start = filter_end = None

            inscriptions: List[Tuple[str, str]] = []  # (EDCS-ID line, cleaned_text)

            for edcs_id_line, raw_lines in blocks:
                original_text = "\n".join(raw_lines)
                contains_greek = bool(GREEK_RANGE_RE.search(original_text))
                if greek_filter == "Exclude" and contains_greek:
                    continue
                if greek_filter == "Greek Only" and not contains_greek:
                    continue

                if filter_start is not None:
                    m = DATE_RE.search(original_text)
                    if not m:
                        continue
                    start, end = map(int, m.groups())
                    if end < filter_start or start > filter_end:
                        continue

                cleaned = self.clean_inscription_lines(raw_lines, greek_filter == "Greek Only")
                cleaned_chars = cleaned.replace('\n', '').strip()
                if cleaned and (not self.exclude_short.get() or len(cleaned_chars) > 3):
                    inscriptions.append((edcs_id_line, cleaned))

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

            # Deterministic selection for this dataset via random.sample with seeded RNG
            subset_pairs = random.sample(inscriptions, n)  # [(header_line, text), ...]

            # Render for file/UI (combined corpus text)
            rendered_blocks = [f"****\n{eid}\n{text}\n" for (eid, text) in subset_pairs]
            corpus_text = "\n".join(rendered_blocks)
            Path(save_path).write_text(corpus_text, encoding="utf-8")

            # Build snapshot payload: use BARE IDs for selection.csv
            selection_ordered = [(normalize_edcs_id(eid_line), text) for (eid_line, text) in subset_pairs]

            filters = {
                "greek": self.greek_option.get(),
                "date_filter_enabled": bool(self.date_filter_enabled.get()),
                "start_year": int(self.start_year_spin.get()) if self.date_filter_enabled.get() else None,
                "end_year": int(self.end_year_spin.get()) if self.date_filter_enabled.get() else None,
                "exclude_fragments": bool(self.exclude_short.get()),
                "dataset": self.dataset_var.get(),
            }
            run_dir = write_seed_snapshot(
                seed=str(seed_value),
                selection_ordered=selection_ordered,
                corpus_text=corpus_text,              # NEW: single-file snapshot
                source_files=[dataset_path],
                mode="Normalized (current)",
                filters=filters,
                app_version="1.1.3"
            )

            # UI
            self.info_box.delete("1.0", tk.END)
            self.info_box.insert(tk.END, corpus_text)
            self.status.config(text=f"Saved {n} cleaned inscriptions to: {save_path} (Seed: {seed_value})  |  Snapshot: {run_dir}")

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
            "province:", "place:", "findspot:", "material:", "author", "editor",
            "status:", "genus:", "comment:", "comments:", "inscriptiones", "publication:",
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

