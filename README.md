# FAB Proxy Forge

Use this to make print-ready proxy sheets with borders + cut marks.

Need full details? See `IN_DEPTH.md`.

<img width="160" height="148" alt="Image" src="https://github.com/user-attachments/assets/be158778-b58a-4743-92c1-6547c2995ca3" /> 

Support: [buymeacoffee.com/dinnermonster](https://buymeacoffee.com/dinnermonster)

## macOS (recommended)

Run the helper script:

```bash
chmod +x ./run.sh
./run.sh ./examples/blank-example.pdf
```

Watch mode (auto-process PDFs in `./pdf/look-for-names`):

```bash
./run.sh --watch
```

Singles mode (8 files ending in `-single.pdf` -> one 4x4 sheet):

```bash
./run.sh --singles-mode --watch
```

## Windows

Install:
- Python 3
- Poppler (`pdftoppm` must be on PATH)

Run in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
py -3 -m pip install --upgrade pip pillow
py -3 .\add_proxy_cut_guides.py .\examples\blank-example.pdf
```

Watch mode:

```powershell
py -3 .\add_proxy_cut_guides.py --watch
```

Singles watch mode:

```powershell
py -3 .\add_proxy_cut_guides.py --singles-mode --watch
```

## Where files go

- Input watch folder: `./pdf/look-for-names`
- Processed output: `./processed`
- Processed source PDFs are moved to: `./pdf_archive`

## Naming for singles

Singles mode only uses files that end with:

- `-single.pdf`
