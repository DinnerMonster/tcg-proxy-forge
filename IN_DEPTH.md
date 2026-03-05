# FAB Proxy Forge (In Depth)

This is the full reference for `add_proxy_cut_guides.py` and `run.sh`.

## Project goal

Turn raw proxy PDFs into print-ready sheets with:
- black card borders
- short gray cut marks
- optional page-edge cut marks
- stable output naming
- automatic archiving of source PDFs after success

## Main files

- `add_proxy_cut_guides.py`: core processor (single, watch, singles modes)
- `run.sh`: macOS/Linux bootstrap runner (checks Python + venv + Pillow + Poppler)
- `examples/blank-example.pdf`: sample test input
- `tests/test_scripts.py`: unit tests

## Processing modes

### 1) Single-file mode

Process one input PDF:

```bash
python3 add_proxy_cut_guides.py ./my-sheet.pdf
```

### 2) Watch mode

Continuously scan a folder for PDFs and process new/unprocessed files:

```bash
python3 add_proxy_cut_guides.py --watch
```

Default watch folder:
- `./pdf`

### 3) Singles mode

Builds 4x4 sheets from `-single.pdf` files.

- consumes `8` singles per sheet by default
- outputs one 4x4 page (16 slots)
- each of the 8 singles is duplicated once to fill 16 slots

One-shot singles:

```bash
python3 add_proxy_cut_guides.py --singles-mode
```

Continuous singles watcher:

```bash
python3 add_proxy_cut_guides.py --singles-mode --watch
```

## Input/output behavior

### Processed output path

By default:

- `./processed/<input-stem>-<settings>-processed.pdf`

`<settings>` is a slug built from major options, for example:

- `rows2-cols4-dpi600-gut0-border0p6-cutlen0p18-cutpt0p6-gray90-thr245-edgemarkson`

You can override output for single-file mode with:

- `-o ./processed/custom-name.pdf`

### Archiving source PDFs

After a successful process:

- source PDF is moved to `./pdf_archive` (default)

If a same filename already exists in archive:

- suffixes are auto-added (`-1`, `-2`, etc.)

### Skip behavior in watch mode

Watch mode skips an input when its expected processed filename already exists in `--processed-dir`.

## CLI options

### Core layout options

- `--rows` (default: `2`)
- `--cols` (default: `4`)
- `--dpi` (default: `600`)
- `--gutter-in` (default: `0.1`)
- `--border-pt` (default: `0.8`)
- `--cut-mark-len-in` (default: `0.18`)
- `--cut-mark-pt` (default: `0.6`)
- `--cut-mark-gray` (default: `90`, range `0..255`)
- `--content-threshold` (default: `245`)
- `--page-edge-marks` / `--no-page-edge-marks` (default: on)

### Mode options

- `--watch`
- `--watch-dir` (default: `./pdf`)
- `--watch-interval` (default: `3.0`)
- `--watch-recursive`

### Folder options

- `--processed-dir` (default: `./processed`)
- `--archive-dir` (default: `./pdf_archive`)

### Singles options

- `--singles-mode`
- `--single-suffix` (default: `-single.pdf`)
- `--singles-batch-size` (default: `8`)
- `--single-max-side-px` (default: `4000`) limits singles render size to avoid huge-memory renders

## Common recipes

### Tight cut setup (no white gutters)

```bash
python3 add_proxy_cut_guides.py ./sheet.pdf \
  --rows 2 --cols 4 --dpi 600 \
  --gutter-in 0 \
  --border-pt 0.6 \
  --cut-mark-pt 0.6
```

### Longer edge/internal cut marks

```bash
python3 add_proxy_cut_guides.py ./sheet.pdf --cut-mark-len-in 0.30
```

### Darker cut marks

```bash
python3 add_proxy_cut_guides.py ./sheet.pdf --cut-mark-gray 60
```

### Disable edge cut marks

```bash
python3 add_proxy_cut_guides.py ./sheet.pdf --no-page-edge-marks
```

### Singles from custom suffix

```bash
python3 add_proxy_cut_guides.py --singles-mode --watch \
  --watch-dir ./pdf \
  --single-suffix _single.pdf \
  --singles-batch-size 8
```

## run.sh behavior

`run.sh` does this before running the Python script:

1. Checks Python exists (`python3` or `python`)
2. Can auto-install Python on macOS if:
   - Homebrew exists, and
   - `AUTO_INSTALL_PYTHON=1` is set
3. Verifies Python has `venv`
4. Checks `pdftoppm` exists
5. Creates `.venv` if needed
6. Ensures `pip` exists inside `.venv`
7. Installs Pillow in `.venv` if missing
8. Runs `add_proxy_cut_guides.py` with passed arguments

Useful env vars:
- `PYTHON_BIN=/path/to/python`
- `AUTO_INSTALL_PYTHON=1`
- `NO_COLOR=1` (disable ANSI color output)

## Testing

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

CI:
- GitHub Actions workflow at `.github/workflows/tests.yml`
- runs on pull requests

## Troubleshooting

### `pdftoppm` not found

Install Poppler and ensure `pdftoppm` is on PATH.

### Python not found

Install Python 3 and rerun `./run.sh`.

### Watch mode appears idle

Check:
- files are in `--watch-dir`
- files are `.pdf`
- for singles mode, names end with `--single-suffix` (default `-single.pdf`)
- enough singles exist for a batch (`--singles-batch-size`, default `8`)

### File was skipped

Expected processed output already exists in `--processed-dir`.

### `Bogus memory allocation size` in singles mode

This usually means a source PDF has a very large page size and was too expensive to render at your current DPI.

Use/adjust:

```bash
--single-max-side-px 4000
```

Lower values reduce memory usage further.
