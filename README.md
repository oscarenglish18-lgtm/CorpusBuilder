EDCS CorpusBuilder v1.2.0
A reproducible corpus generation tool for Latin epigraphic research, built on data from the Epigraphik-Datenbank Clauss-Slaby (EDCS). Developed as part of an undergraduate dissertation at the University of Southampton (2026).

Overview
EDCS CorpusBuilder is a GUI application that generates cleaned, structured corpora of Latin inscriptions from local EDCS data exports. Corpora are deterministically sampled using SHA256 hash-ranking, meaning any corpus can be exactly reproduced on any machine using the same seed token and source data.

Requirements

Python 3.8+
No external dependencies — uses Python standard library only (tkinter, hashlib, csv, json, pathlib)

Or, run the pre-built Windows executable (EDCS_CorpusBuilder.exe).Raw EDCS data must be downloaded and placed in the data folder. 

Installation
From source:https://github.com/oscarenglish18-lgtm/CorpusBuilder
python CorpusBuid0.9.py
From exe:
Download EDCS_CorpusBuilder.exe from the Releases page and run directly. No installation required.

Folder Structure
The tool expects the following layout:
/
├── CorpusBuilder0.9.py
├── EDCS_CorpusBuilder.exe
├── data/
│   ├── thugga.txt
│   ├── lambaesis.txt
│   ├── rome.txt
│   └── ... (other EDCS export data)
└── data/snapshots/
    └── seed-<TOKEN>/
        ├── corpus.txt
        ├── selection.csv
        ├── manifest.json
        └── README.txt
Data files must be placed in a data/ folder in the same directory as the script or exe. Files must be named after the site (e.g. thugga.txt, lambaesis.txt) for the dataset code system to work correctly.

Dataset Codes
Each site has a short code used in seed tokens and the dataset selector:

Usage
Basic workflow

Launch the application
Select a dataset from the dropdown (or specify via seed token)
Set the number of inscriptions
Configure filters (date range, Greek script, fragments)
Enter or generate a seed token
Click Generate and choose a save location
The corpus is saved as a .txt file ready for AntConc

Filters
Date filter — restrict inscriptions to those dated within a specified range (in years CE/BCE, using negative numbers for BCE). Only inscriptions with a recorded date range that overlaps your window will be included.
Greek script — three options:

Include — all inscriptions (default)
Exclude — Latin only, drops any inscription containing Greek characters
Greek Only — Greek-script inscriptions only

Exclude fragments — removes inscriptions with fewer than 4 characters after cleaning. Recommended for most analyses as fragments are often incomprehensible.

Seed Tokens and Reproducibility
Every corpus is identified by a seed token, which encodes the dataset, sample size, and generation parameters in a single string. Sharing a seed token allows any other user with the same source data to regenerate an identical corpus.
Token format
[CODE-]SEED[:N][|PARAMS]
Examples:
T-ABCD1234EFGH5678:500                    (500 inscriptions from Thugga)
T-ABCD1234EFGH5678:300|D-200_300|GE|F1   (300 inscriptions, dated -200 to 300 CE, Latin only, no fragments)
ALL-ABCD1234EFGH5678:1000                 (1000 inscriptions from all datasets)
ABCD1234EFGH5678:200                      (200 inscriptions from whichever dataset is selected in the UI)
Parameter codes in tokens

Selection uses SHA256 hash-ranking:
score(inscription) = SHA256(seed | dataset_fingerprint | EDCS_ID)
Inscriptions are ranked by their score and the top N are selected. This method is stable across operating systems and Python versions, and does not rely on Python's random module (which is not reproducible across versions). The dataset fingerprint is a SHA256 hash of the source file contents, ensuring that any change to the underlying data is detected.

Output Format
Each corpus file is a plain text file with inscriptions separated by ****:
****
EDCS-ID: EDCS-12345678
DMS
IVLIA PIA VIXIT
ANNIS XXXV
HSE
****
EDCS-ID: EDCS-87654321
...
This format is directly importation into other programs for analysis
Text normalisation
The cleaner applies the following transformations:

Metadata lines (province, place, findspot, material, etc.) are stripped
Editorial additions in ( ) and < > are removed
Restorations in [ ] are kept (brackets removed, letters preserved)
U and u are converted to V (epigraphic convention)
Text is uppercased
Common formulaic abbreviations are consolidated (e.g. D M S → DMS, H S E → HSE)


Snapshots
Every corpus generation automatically writes a snapshot to data/snapshots/seed-<TOKEN>/ containing:

corpus.txt — the full corpus text
selection.csv — ordered list of EDCS IDs selected
manifest.json — full provenance record (seed, timestamp, filters, source file hashes, app version)
README.txt — brief description

Snapshots allow corpora to be audited and verified independently of re-running the tool.

Corpora Generated for Dissertation
The following seed tokens were used to generate the seven corpora analysed in the dissertation. Source data: EDCS (accessed 2025–2026).
CorpusCodeTokenNThugga (full)T[TOKEN]2048Thugga (dated)T[TOKEN][N]Lambaesis (full)L[TOKEN][N]Lambaesis (dated)L[TOKEN][N]Thibursicum BureTB[TOKEN][N]Uchi MaiusUM[TOKEN][N]RomeR[TOKEN][N]
(Fill in actual tokens from your snapshot manifest.json files)

Data
Inscription data is sourced from the EDCS. See LICENCE.txt for copyright information.
