#!/usr/bin/env python3
"""Add card borders, spacing, and cut-guide ticks to a proxy sheet PDF.

Workflow:
1) Render PDF pages to PNG with `pdftoppm`.
2) Auto-detect the non-white card block.
3) Split that block into a rows x cols grid of cards.
4) Re-compose on the same page size with configurable gutters.
5) Draw black borders around each card + short dark-gray cut marks.
6) Save back to a PDF.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_pdf",
        nargs="?",
        type=Path,
        help="Input proxy sheet PDF (omit when using --watch)",
    )
    parser.add_argument(
        "-o",
        "--output-pdf",
        type=Path,
        default=None,
        help=(
            "Output PDF path override. If omitted, writes to "
            "<processed-dir>/<input-stem>-<all-settings>-processed.pdf"
        ),
    )
    parser.add_argument("--rows", type=int, default=2, help="Card rows (default: 2)")
    parser.add_argument("--cols", type=int, default=4, help="Card columns (default: 4)")
    parser.add_argument("--dpi", type=int, default=600, help="Render DPI (default: 600)")
    parser.add_argument(
        "--gutter-in",
        type=float,
        default=0.1,
        help="Spacing between cards in inches (default: 0.1)",
    )
    parser.add_argument(
        "--border-pt",
        type=float,
        default=0.8,
        help="Card border thickness in points (default: 0.8)",
    )
    parser.add_argument(
        "--cut-mark-len-in",
        type=float,
        default=0.18,
        help="Cut mark length in inches (default: 0.18)",
    )
    parser.add_argument(
        "--cut-mark-pt",
        type=float,
        default=0.6,
        help="Cut mark thickness in points (default: 0.6)",
    )
    parser.add_argument(
        "--cut-mark-gray",
        type=int,
        default=90,
        help="Cut mark gray value (0=black, 255=white; default: 90)",
    )
    parser.add_argument(
        "--content-threshold",
        type=int,
        default=245,
        help="Auto-crop white threshold (default: 245)",
    )
    parser.add_argument(
        "--page-edge-marks",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Draw cut marks on page edges (default: on)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch a directory and auto-process PDFs that are missing outputs",
    )
    parser.add_argument(
        "--watch-dir",
        type=Path,
        default=Path("./pdf/look-for-names"),
        help="Directory to watch when --watch is set (default: ./pdf/look-for-names)",
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=3.0,
        help="Polling interval in seconds for watch mode (default: 3.0)",
    )
    parser.add_argument(
        "--watch-recursive",
        action="store_true",
        help="Recursively scan subdirectories in watch mode",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=Path("./processed"),
        help="Directory for processed PDFs (default: ./processed)",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path("./pdf_archive"),
        help="Directory where processed source PDFs are moved (default: ./pdf_archive)",
    )
    parser.add_argument(
        "--singles-mode",
        action="store_true",
        help="Build 4x4 sheets from batches of single-card PDFs ending in -single.pdf",
    )
    parser.add_argument(
        "--single-suffix",
        type=str,
        default="-single.pdf",
        help="Filename suffix to match single-card PDFs (default: -single.pdf)",
    )
    parser.add_argument(
        "--singles-batch-size",
        type=int,
        default=8,
        help="How many single PDFs to consume per 4x4 sheet (default: 8)",
    )
    args = parser.parse_args()
    if args.rows < 1 or args.cols < 1:
        parser.error("--rows and --cols must be >= 1")
    if not (0 <= args.cut_mark_gray <= 255):
        parser.error("--cut-mark-gray must be in [0,255]")
    if args.dpi < 72:
        parser.error("--dpi must be >= 72")
    if args.watch_interval <= 0:
        parser.error("--watch-interval must be > 0")
    if args.singles_batch_size < 1:
        parser.error("--singles-batch-size must be >= 1")
    if args.singles_mode:
        if args.input_pdf is not None:
            parser.error("Do not pass input_pdf when using --singles-mode")
        if args.output_pdf is not None:
            parser.error("--output-pdf is not supported with --singles-mode")
    elif args.watch:
        if args.input_pdf is not None:
            parser.error("Do not pass input_pdf when using --watch")
        if args.output_pdf is not None:
            parser.error("--output-pdf is not supported with --watch")
    elif args.input_pdf is None:
        parser.error("input_pdf is required unless --watch or --singles-mode is set")
    return args


def color_enabled() -> bool:
    return sys.stdout.isatty() and os.getenv("TERM") not in (None, "dumb") and os.getenv("NO_COLOR") is None


def style(text: str, code: str) -> str:
    if not color_enabled():
        return text
    return f"\033[{code}m{text}\033[0m"


def banner(title: str) -> None:
    print(style(f"== {title} ==", "1;36"), flush=True)


def kv(label: str, value: str) -> None:
    print(f"{style(label + ':', '1;34'):18} {value}", flush=True)


def event(tag: str, message: str, code: str = "0") -> None:
    print(f"{style('[' + tag + ']', code)} {message}", flush=True)


def page_sort_key(path: Path) -> int:
    # pdftoppm names files like "<prefix>-1.png", "<prefix>-2.png", ...
    match = re.search(r"-(\d+)$", path.stem)
    return int(match.group(1)) if match else 0


def detect_content_bbox(image: Image.Image, threshold: int) -> tuple[int, int, int, int]:
    # Detect any non-white pixel by thresholding to a mask and taking bbox.
    gray = image.convert("L")
    mask = gray.point(lambda p: 255 if p < threshold else 0, mode="1")
    bbox = mask.getbbox()
    if bbox is None:
        return (0, 0, image.width, image.height)
    return bbox


def compose_page(image: Image.Image, args: argparse.Namespace) -> Image.Image:
    image = image.convert("RGB")
    page_w, page_h = image.size
    bbox = detect_content_bbox(image, args.content_threshold)
    x1, y1, x2, y2 = bbox
    content_w = x2 - x1
    content_h = y2 - y1

    col_edges = [round(i * content_w / args.cols) for i in range(args.cols + 1)]
    row_edges = [round(i * content_h / args.rows) for i in range(args.rows + 1)]
    col_widths = [col_edges[i + 1] - col_edges[i] for i in range(args.cols)]
    row_heights = [row_edges[i + 1] - row_edges[i] for i in range(args.rows)]

    gutter_px = max(0, round(args.gutter_in * args.dpi))
    border_px = max(1, round(args.border_pt * args.dpi / 72))
    tick_len_px = max(2, round(args.cut_mark_len_in * args.dpi))
    tick_w_px = max(1, round(args.cut_mark_pt * args.dpi / 72))

    grid_w = content_w + gutter_px * (args.cols - 1)
    grid_h = content_h + gutter_px * (args.rows - 1)

    if grid_w > page_w or grid_h > page_h:
        raise ValueError(
            "Grid with gutters does not fit on page. "
            f"grid={grid_w}x{grid_h}, page={page_w}x{page_h}. "
            "Use smaller --gutter-in, fewer rows/cols, or a larger page."
        )

    grid_left = (page_w - grid_w) // 2
    grid_top = (page_h - grid_h) // 2
    grid_right = grid_left + grid_w
    grid_bottom = grid_top + grid_h

    out = Image.new("RGB", (page_w, page_h), "white")
    draw = ImageDraw.Draw(out)
    black = (0, 0, 0)
    gray = (args.cut_mark_gray, args.cut_mark_gray, args.cut_mark_gray)

    for r in range(args.rows):
        for c in range(args.cols):
            sx1 = x1 + col_edges[c]
            sy1 = y1 + row_edges[r]
            sx2 = x1 + col_edges[c + 1]
            sy2 = y1 + row_edges[r + 1]

            dx1 = grid_left + col_edges[c] + c * gutter_px
            dy1 = grid_top + row_edges[r] + r * gutter_px
            dx2 = dx1 + (sx2 - sx1)
            dy2 = dy1 + (sy2 - sy1)

            card = image.crop((sx1, sy1, sx2, sy2))
            out.paste(card, (dx1, dy1))
            draw.rectangle((dx1, dy1, dx2 - 1, dy2 - 1), outline=black, width=border_px)

    # Internal cut lines are centered in gutters.
    internal_cut_xs: list[int] = []
    for c in range(1, args.cols):
        cut_x = int(round(grid_left + sum(col_widths[:c]) + (c - 0.5) * gutter_px))
        internal_cut_xs.append(cut_x)
        draw.line(
            (cut_x, grid_top - tick_len_px // 2, cut_x, grid_top + tick_len_px // 2),
            fill=gray,
            width=tick_w_px,
        )
        draw.line(
            (cut_x, grid_bottom - tick_len_px // 2, cut_x, grid_bottom + tick_len_px // 2),
            fill=gray,
            width=tick_w_px,
        )

    internal_cut_ys: list[int] = []
    for r in range(1, args.rows):
        cut_y = int(round(grid_top + sum(row_heights[:r]) + (r - 0.5) * gutter_px))
        internal_cut_ys.append(cut_y)
        draw.line(
            (grid_left - tick_len_px // 2, cut_y, grid_left + tick_len_px // 2, cut_y),
            fill=gray,
            width=tick_w_px,
        )
        draw.line(
            (grid_right - tick_len_px // 2, cut_y, grid_right + tick_len_px // 2, cut_y),
            fill=gray,
            width=tick_w_px,
        )

    if args.page_edge_marks:
        edge_tick_len = tick_len_px
        vertical_cuts = [grid_left] + internal_cut_xs + [grid_right]
        horizontal_cuts = [grid_top] + internal_cut_ys + [grid_bottom]

        for cut_x in vertical_cuts:
            x = max(0, min(page_w - 1, cut_x))
            draw.line(
                (x, 0, x, min(page_h - 1, edge_tick_len - 1)),
                fill=gray,
                width=tick_w_px,
            )
            draw.line(
                (x, max(0, page_h - edge_tick_len), x, page_h - 1),
                fill=gray,
                width=tick_w_px,
            )

        for cut_y in horizontal_cuts:
            y = max(0, min(page_h - 1, cut_y))
            draw.line(
                (0, y, min(page_w - 1, edge_tick_len - 1), y),
                fill=gray,
                width=tick_w_px,
            )
            draw.line(
                (max(0, page_w - edge_tick_len), y, page_w - 1, y),
                fill=gray,
                width=tick_w_px,
            )

    return out


def render_pdf_to_pngs(input_pdf: Path, dpi: int, workdir: Path) -> list[Path]:
    prefix = workdir / "page"
    cmd = [
        "pdftoppm",
        "-png",
        "-r",
        str(dpi),
        str(input_pdf),
        str(prefix),
    ]
    subprocess.run(cmd, check=True)
    pages = sorted(workdir.glob("page-*.png"), key=page_sort_key)
    if not pages:
        raise RuntimeError("No pages were rendered by pdftoppm.")
    return pages


def fmt_setting(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.replace("-", "neg").replace(".", "p")


def settings_slug(args: argparse.Namespace) -> str:
    parts = [
        f"rows{args.rows}",
        f"cols{args.cols}",
        f"dpi{args.dpi}",
        f"gut{fmt_setting(args.gutter_in)}",
        f"border{fmt_setting(args.border_pt)}",
        f"cutlen{fmt_setting(args.cut_mark_len_in)}",
        f"cutpt{fmt_setting(args.cut_mark_pt)}",
        f"gray{args.cut_mark_gray}",
        f"thr{args.content_threshold}",
        f"edgemarks{'on' if args.page_edge_marks else 'off'}",
    ]
    return "-".join(parts)


def settings_summary(args: argparse.Namespace) -> str:
    return (
        f"rows={args.rows}, cols={args.cols}, dpi={args.dpi}, "
        f"gutter_in={args.gutter_in}, border_pt={args.border_pt}, "
        f"cut_mark_len_in={args.cut_mark_len_in}, cut_mark_pt={args.cut_mark_pt}, "
        f"cut_mark_gray={args.cut_mark_gray}, content_threshold={args.content_threshold}, "
        f"page_edge_marks={args.page_edge_marks}"
    )


def print_settings_block(args: argparse.Namespace) -> None:
    kv("Rows x Cols", f"{args.rows} x {args.cols}")
    kv("DPI", str(args.dpi))
    kv("Gutter (in)", str(args.gutter_in))
    kv("Border (pt)", str(args.border_pt))
    kv("Cut len (in)", str(args.cut_mark_len_in))
    kv("Cut width (pt)", str(args.cut_mark_pt))
    kv("Cut gray", str(args.cut_mark_gray))
    kv("Threshold", str(args.content_threshold))
    kv("Edge marks", str(args.page_edge_marks))


def default_output_path(input_pdf: Path, args: argparse.Namespace) -> Path:
    file_name = f"{input_pdf.stem}-{settings_slug(args)}-processed.pdf"
    return args.processed_dir.resolve() / file_name


def next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    index = 1
    while True:
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def archive_input_pdf(input_pdf: Path, args: argparse.Namespace) -> Path:
    archive_dir = args.archive_dir.resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = next_available_path(archive_dir / input_pdf.name)
    shutil.move(str(input_pdf), str(target))
    event("ARCHIVE", f"{input_pdf.name} -> {target}", "35")
    return target


def render_single_pdf_to_image(input_pdf: Path, dpi: int, workdir: Path, prefix: str) -> Image.Image:
    out_prefix = workdir / prefix
    cmd = [
        "pdftoppm",
        "-png",
        "-singlefile",
        "-f",
        "1",
        "-l",
        "1",
        "-r",
        str(dpi),
        str(input_pdf),
        str(out_prefix),
    ]
    subprocess.run(cmd, check=True)
    png_path = out_prefix.with_suffix(".png")
    if not png_path.exists():
        raise RuntimeError(f"Failed to render single PDF: {input_pdf}")
    with Image.open(png_path) as img:
        return img.convert("RGB")


def fit_card_to_cell(card: Image.Image, cell_w: int, cell_h: int) -> Image.Image:
    resampling = getattr(Image, "Resampling", Image)
    resized = card.copy()
    resized.thumbnail((cell_w, cell_h), resampling.LANCZOS)
    canvas = Image.new("RGB", (cell_w, cell_h), "white")
    x = (cell_w - resized.width) // 2
    y = (cell_h - resized.height) // 2
    canvas.paste(resized, (x, y))
    return canvas


def iter_single_pdfs(watch_dir: Path, recursive: bool, suffix: str) -> list[Path]:
    candidates: list[Path] = []
    suffix = suffix.lower()
    walker = watch_dir.rglob("*") if recursive else watch_dir.iterdir()
    for path in walker:
        if not path.is_file():
            continue
        if not path.name.lower().endswith(suffix):
            continue
        if "processed" in path.parts or "pdf_archive" in path.parts:
            continue
        candidates.append(path.resolve())
    return sorted(candidates)


def singles_output_path(args: argparse.Namespace) -> Path:
    sheet_args = argparse.Namespace(**vars(args))
    sheet_args.rows = 4
    sheet_args.cols = 4
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    file_name = f"singles-batch-{timestamp}-{settings_slug(sheet_args)}-processed.pdf"
    return next_available_path(args.processed_dir.resolve() / file_name)


def process_singles_batch(single_pdfs: list[Path], args: argparse.Namespace) -> Path:
    if not single_pdfs:
        raise ValueError("No singles provided for batch processing.")

    event("SINGLES", f"Building 4x4 sheet from {len(single_pdfs)} single PDFs", "1;35")
    for pdf in single_pdfs:
        kv("Input single", pdf.name)

    page_w = int(round(11 * args.dpi))
    page_h = int(round(8.5 * args.dpi))

    cards: list[Image.Image] = []
    with tempfile.TemporaryDirectory(prefix="proxy-singles-") as temp_dir:
        temp_path = Path(temp_dir)
        for idx, single_pdf in enumerate(single_pdfs):
            card_img = render_single_pdf_to_image(single_pdf, args.dpi, temp_path, f"single-{idx}")
            bbox = detect_content_bbox(card_img, args.content_threshold)
            cards.append(card_img.crop(bbox))

    max_w = max(card.width for card in cards)
    max_h = max(card.height for card in cards)
    scale = min(page_w / (4 * max_w), page_h / (4 * max_h), 1.0)
    cell_w = max(1, int(max_w * scale))
    cell_h = max(1, int(max_h * scale))

    prepared_cards = [fit_card_to_cell(card, cell_w, cell_h) for card in cards]

    # For 8 singles, each card is duplicated once to fill 16 slots.
    if len(prepared_cards) == 8:
        grid_cards = [card for card in prepared_cards for _ in (0, 1)]
    else:
        grid_cards = []
        idx = 0
        while len(grid_cards) < 16:
            grid_cards.append(prepared_cards[idx % len(prepared_cards)])
            idx += 1
    grid_cards = grid_cards[:16]

    source_page = Image.new("RGB", (page_w, page_h), "white")
    grid_w = cell_w * 4
    grid_h = cell_h * 4
    grid_left = (page_w - grid_w) // 2
    grid_top = (page_h - grid_h) // 2

    for idx, card in enumerate(grid_cards):
        row = idx // 4
        col = idx % 4
        x = grid_left + col * cell_w
        y = grid_top + row * cell_h
        source_page.paste(card, (x, y))

    sheet_args = argparse.Namespace(**vars(args))
    sheet_args.rows = 4
    sheet_args.cols = 4
    final_page = compose_page(source_page, sheet_args)

    output_pdf = singles_output_path(args)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    final_page.save(output_pdf, format="PDF", resolution=float(args.dpi))
    event("DONE", f"Singles sheet -> {output_pdf.name}", "1;32")

    for single_pdf in single_pdfs:
        archive_input_pdf(single_pdf, args)

    return output_pdf


def singles_mode_once(args: argparse.Namespace) -> None:
    watch_dir = args.watch_dir.resolve()
    if not watch_dir.exists():
        watch_dir.mkdir(parents=True, exist_ok=True)
        event("INFO", f"Created singles directory: {watch_dir}", "36")
    elif not watch_dir.is_dir():
        raise NotADirectoryError(f"Singles directory is not a directory: {watch_dir}")

    singles = iter_single_pdfs(watch_dir, args.watch_recursive, args.single_suffix)
    if len(singles) < args.singles_batch_size:
        event(
            "INFO",
            f"Found {len(singles)} singles with suffix '{args.single_suffix}', need {args.singles_batch_size}",
            "36",
        )
        return

    process_singles_batch(singles[: args.singles_batch_size], args)


def watch_singles_mode(args: argparse.Namespace) -> None:
    watch_dir = args.watch_dir.resolve()
    if not watch_dir.exists():
        watch_dir.mkdir(parents=True, exist_ok=True)
        event("INFO", f"Created singles directory: {watch_dir}", "36")
    elif not watch_dir.is_dir():
        raise NotADirectoryError(f"Singles directory is not a directory: {watch_dir}")

    banner("Singles Watch Mode")
    kv("Watch dir", str(watch_dir))
    kv("Suffix", args.single_suffix)
    kv("Batch size", str(args.singles_batch_size))
    kv("Output grid", "4 x 4")
    kv("Processed dir", str(args.processed_dir.resolve()))
    kv("Archive dir", str(args.archive_dir.resolve()))
    kv("Interval", f"{args.watch_interval:.1f}s")
    kv("Recursive", str(args.watch_recursive))
    print(style("Settings:", "1;34"), flush=True)
    print_settings_block(args)

    last_count: int | None = None
    while True:
        singles = iter_single_pdfs(watch_dir, args.watch_recursive, args.single_suffix)
        if len(singles) != last_count:
            event(
                "INFO",
                f"Singles pending: {len(singles)} (need {args.singles_batch_size} per sheet)",
                "36",
            )
            last_count = len(singles)

        while len(singles) >= args.singles_batch_size:
            process_singles_batch(singles[: args.singles_batch_size], args)
            singles = iter_single_pdfs(watch_dir, args.watch_recursive, args.single_suffix)
            last_count = len(singles)
            event(
                "INFO",
                f"Singles pending: {len(singles)} (need {args.singles_batch_size} per sheet)",
                "36",
            )
        time.sleep(args.watch_interval)


def process_pdf(input_pdf: Path, args: argparse.Namespace, output_pdf: Path | None = None) -> Path:
    input_pdf = input_pdf.resolve()
    if not input_pdf.exists():
        raise FileNotFoundError(f"Input PDF not found: {input_pdf}")

    if output_pdf is None:
        output_pdf = default_output_path(input_pdf, args)
    output_pdf = output_pdf.resolve()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    event("PROCESS", input_pdf.name, "1;32")
    kv("Output", str(output_pdf))
    kv("Settings", settings_summary(args))

    with tempfile.TemporaryDirectory(prefix="proxy-cut-guides-") as temp_dir:
        temp_path = Path(temp_dir)
        event("RENDER", f"Rendering pages at {args.dpi} DPI", "36")
        page_pngs = render_pdf_to_pngs(input_pdf, args.dpi, temp_path)
        composed_pages: list[Image.Image] = []
        for idx, page_png in enumerate(page_pngs, start=1):
            event("COMPOSE", f"Page {idx}/{len(page_pngs)}", "36")
            with Image.open(page_png) as page_img:
                composed_pages.append(compose_page(page_img, args))

    first, rest = composed_pages[0], composed_pages[1:]
    save_kwargs = {
        "format": "PDF",
        "resolution": float(args.dpi),
    }
    if rest:
        save_kwargs["save_all"] = True
        save_kwargs["append_images"] = rest
    first.save(output_pdf, **save_kwargs)

    event("DONE", f"{input_pdf.name} -> {output_pdf.name}", "1;32")
    archive_input_pdf(input_pdf, args)
    return output_pdf


def iter_watch_pdfs(watch_dir: Path, recursive: bool) -> list[Path]:
    candidates: list[Path] = []
    walker = watch_dir.rglob("*") if recursive else watch_dir.iterdir()
    for path in walker:
        if not path.is_file():
            continue
        if path.suffix.lower() != ".pdf":
            continue
        if "processed" in path.parts:
            continue
        candidates.append(path.resolve())
    return sorted(candidates)


def watch_mode(args: argparse.Namespace) -> None:
    watch_dir = args.watch_dir.resolve()
    if not watch_dir.exists():
        watch_dir.mkdir(parents=True, exist_ok=True)
        event("INFO", f"Created watch directory: {watch_dir}", "36")
    elif not watch_dir.is_dir():
        raise NotADirectoryError(f"Watch directory is not a directory: {watch_dir}")

    banner("Watch Mode")
    kv("Watch dir", str(watch_dir))
    kv("Processed dir", str(args.processed_dir.resolve()))
    kv("Archive dir", str(args.archive_dir.resolve()))
    kv("Interval", f"{args.watch_interval:.1f}s")
    kv("Recursive", str(args.watch_recursive))
    kv("Output naming", "<input-stem>-<settings>-processed.pdf")
    print(style("Settings:", "1;34"), flush=True)
    print_settings_block(args)

    seen_skips: set[Path] = set()
    while True:
        candidates = iter_watch_pdfs(watch_dir, args.watch_recursive)
        for input_pdf in candidates:
            output_pdf = default_output_path(input_pdf, args).resolve()
            if output_pdf.exists():
                if input_pdf not in seen_skips:
                    event("SKIP", f"{input_pdf.name} (already processed)", "1;33")
                    seen_skips.add(input_pdf)
                continue
            try:
                process_pdf(input_pdf, args, output_pdf)
                seen_skips.discard(input_pdf)
            except Exception as exc:
                event("ERROR", f"{input_pdf.name}: {exc}", "1;31")
        time.sleep(args.watch_interval)


def main() -> None:
    args = parse_args()
    if args.singles_mode:
        try:
            if args.watch:
                watch_singles_mode(args)
            else:
                banner("Singles Mode")
                kv("Watch dir", str(args.watch_dir.resolve()))
                kv("Suffix", args.single_suffix)
                kv("Batch size", str(args.singles_batch_size))
                kv("Output grid", "4 x 4")
                kv("Processed dir", str(args.processed_dir.resolve()))
                kv("Archive dir", str(args.archive_dir.resolve()))
                print(style("Settings:", "1;34"), flush=True)
                print_settings_block(args)
                singles_mode_once(args)
        except KeyboardInterrupt:
            event("STOP", "Singles watcher stopped.", "1;35")
        return

    if args.watch:
        try:
            watch_mode(args)
        except KeyboardInterrupt:
            event("STOP", "Watcher stopped.", "1;35")
        return

    input_pdf = args.input_pdf.resolve()
    output_pdf = args.output_pdf.resolve() if args.output_pdf else None
    banner("Single File Mode")
    kv("Input", str(input_pdf))
    kv("Processed dir", str(args.processed_dir.resolve()))
    kv("Archive dir", str(args.archive_dir.resolve()))
    kv("Output naming", "<input-stem>-<settings>-processed.pdf")
    print(style("Settings:", "1;34"), flush=True)
    print_settings_block(args)
    process_pdf(input_pdf, args, output_pdf)


if __name__ == "__main__":
    main()
