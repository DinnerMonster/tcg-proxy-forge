import argparse
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "add_proxy_cut_guides.py"
RUN_SH_PATH = REPO_ROOT / "run.sh"


def load_script_module():
    spec = importlib.util.spec_from_file_location("add_proxy_cut_guides", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


SCRIPT = load_script_module()


def parse_with_argv(argv):
    with mock.patch.object(sys, "argv", ["add_proxy_cut_guides.py", *argv]):
        return SCRIPT.parse_args()


def base_args(tmp: Path, **overrides):
    defaults = {
        "rows": 2,
        "cols": 4,
        "dpi": 600,
        "gutter_in": 0.0,
        "border_pt": 0.6,
        "cut_mark_len_in": 0.18,
        "cut_mark_pt": 0.6,
        "cut_mark_gray": 90,
        "content_threshold": 245,
        "page_edge_marks": True,
        "watch": False,
        "watch_dir": tmp / "watch",
        "watch_interval": 3.0,
        "watch_recursive": False,
        "processed_dir": tmp / "processed",
        "archive_dir": tmp / "archive",
        "singles_mode": False,
        "single_suffix": "-single.pdf",
        "singles_batch_size": 8,
        "single_max_side_px": 4000,
        "output_pdf": None,
        "input_pdf": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class ParseArgsTests(unittest.TestCase):
    def test_requires_input_when_not_watch_or_singles(self):
        with self.assertRaises(SystemExit):
            parse_with_argv([])

    def test_watch_disallows_input_pdf(self):
        with self.assertRaises(SystemExit):
            parse_with_argv(["--watch", "input.pdf"])

    def test_singles_mode_disallows_output_override(self):
        with self.assertRaises(SystemExit):
            parse_with_argv(["--singles-mode", "-o", "out.pdf"])

    def test_singles_mode_allows_no_input(self):
        args = parse_with_argv(["--singles-mode"])
        self.assertTrue(args.singles_mode)
        self.assertIsNone(args.input_pdf)


class HelpersTests(unittest.TestCase):
    def test_default_output_path_uses_processed_dir_and_slug(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            args = base_args(tmp, processed_dir=tmp / "processed")
            input_pdf = tmp / "my-sheet.pdf"
            output = SCRIPT.default_output_path(input_pdf, args)
            self.assertEqual(output.parent, (tmp / "processed").resolve())
            self.assertTrue(output.name.startswith("my-sheet-"))
            self.assertTrue(output.name.endswith("-processed.pdf"))

    def test_next_available_path_appends_increment(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = tmp / "file.pdf"
            target.write_bytes(b"x")
            next_path = SCRIPT.next_available_path(target)
            self.assertEqual(next_path.name, "file-1.pdf")
            next_path.write_bytes(b"x")
            third = SCRIPT.next_available_path(target)
            self.assertEqual(third.name, "file-2.pdf")

    def test_iter_watch_pdfs_ignores_processed_folder(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "a.pdf").write_bytes(b"x")
            (tmp / "b.PDF").write_bytes(b"x")
            (tmp / "note.txt").write_text("ignore")
            processed = tmp / "processed"
            processed.mkdir()
            (processed / "skip.pdf").write_bytes(b"x")
            found = SCRIPT.iter_watch_pdfs(tmp, recursive=True)
            self.assertEqual([p.name for p in found], ["a.pdf", "b.PDF"])

    def test_iter_single_pdfs_matches_suffix_and_skips_archive(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "keep-single.pdf").write_bytes(b"x")
            (tmp / "skip.pdf").write_bytes(b"x")
            arc = tmp / "pdf_archive"
            arc.mkdir()
            (arc / "skip-single.pdf").write_bytes(b"x")
            proc = tmp / "processed"
            proc.mkdir()
            (proc / "skip-single.pdf").write_bytes(b"x")
            found = SCRIPT.iter_single_pdfs(tmp, recursive=True, suffix="-single.pdf")
            self.assertEqual([p.name for p in found], ["keep-single.pdf"])

    def test_choose_single_render_dpi_lowers_for_large_page(self):
        with mock.patch.object(SCRIPT, "get_pdf_page_size_pts", return_value=(4032.0, 3024.0)):
            dpi = SCRIPT.choose_single_render_dpi(Path("big.pdf"), target_dpi=600, max_side_px=4000)
        self.assertEqual(dpi, 71)


class SinglesModeTests(unittest.TestCase):
    def test_singles_mode_once_waits_for_minimum_batch(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            watch_dir = tmp / "watch"
            watch_dir.mkdir()
            for i in range(3):
                (watch_dir / f"card{i}-single.pdf").write_bytes(b"x")
            args = base_args(tmp, singles_mode=True, watch_dir=watch_dir, singles_batch_size=8)
            with mock.patch.object(SCRIPT, "process_singles_batch") as process_mock:
                SCRIPT.singles_mode_once(args)
                process_mock.assert_not_called()

    def test_singles_mode_once_uses_first_batch(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            watch_dir = tmp / "watch"
            watch_dir.mkdir()
            for i in range(10):
                (watch_dir / f"card{i:02d}-single.pdf").write_bytes(b"x")
            args = base_args(tmp, singles_mode=True, watch_dir=watch_dir, singles_batch_size=8)
            with mock.patch.object(SCRIPT, "process_singles_batch") as process_mock:
                SCRIPT.singles_mode_once(args)
                process_mock.assert_called_once()
                sent_batch = process_mock.call_args[0][0]
                self.assertEqual(len(sent_batch), 8)
                self.assertEqual(sent_batch[0].name, "card00-single.pdf")
                self.assertEqual(sent_batch[-1].name, "card07-single.pdf")

    def test_process_singles_batch_creates_pdf_and_archives_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            processed_dir = tmp / "processed"
            processed_dir.mkdir()
            archive_dir = tmp / "archive"
            archive_dir.mkdir()

            singles = []
            for i in range(8):
                p = tmp / f"card{i}-single.pdf"
                p.write_bytes(b"x")
                singles.append(p)

            args = base_args(
                tmp,
                dpi=72,
                processed_dir=processed_dir,
                archive_dir=archive_dir,
            )

            colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (200, 200, 0)] * 2
            images = [Image.new("RGB", (120 + i, 180 + i), colors[i]) for i in range(8)]

            def fake_render(_pdf, _dpi, _workdir, prefix, _max_side_px):
                idx = int(prefix.split("-")[-1])
                return images[idx]

            archived_inputs = []

            def fake_archive(input_pdf, _args):
                archived_inputs.append(input_pdf)
                return archive_dir / input_pdf.name

            out_path = processed_dir / "out.pdf"
            with (
                mock.patch.object(SCRIPT, "render_single_pdf_to_image", side_effect=fake_render),
                mock.patch.object(SCRIPT, "archive_input_pdf", side_effect=fake_archive),
                mock.patch.object(SCRIPT, "singles_output_path", return_value=out_path),
                mock.patch.object(SCRIPT, "event"),
                mock.patch.object(SCRIPT, "kv"),
            ):
                result = SCRIPT.process_singles_batch(singles, args)

            self.assertEqual(result, out_path)
            self.assertTrue(out_path.exists())
            self.assertEqual(len(archived_inputs), 8)

    def test_process_singles_batch_rejects_empty_input(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            args = base_args(tmp)
            with self.assertRaises(ValueError):
                SCRIPT.process_singles_batch([], args)

    def test_process_singles_batch_does_not_repeat_when_batch_is_less_than_grid(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            processed_dir = tmp / "processed"
            processed_dir.mkdir()
            archive_dir = tmp / "archive"
            archive_dir.mkdir()

            singles = []
            for i in range(7):
                p = tmp / f"card{i}-single.pdf"
                p.write_bytes(b"x")
                singles.append(p)

            args = base_args(
                tmp,
                dpi=72,
                processed_dir=processed_dir,
                archive_dir=archive_dir,
                singles_batch_size=7,
            )

            def fake_render(_pdf, _dpi, _workdir, _prefix, _max_side_px):
                return Image.new("RGB", (120, 180), (255, 0, 0))

            archived_inputs = []

            def fake_archive(input_pdf, _args):
                archived_inputs.append(input_pdf)
                return archive_dir / input_pdf.name

            out_path = processed_dir / "out-7.pdf"
            with (
                mock.patch.object(SCRIPT, "render_single_pdf_to_image", side_effect=fake_render),
                mock.patch.object(SCRIPT, "archive_input_pdf", side_effect=fake_archive),
                mock.patch.object(SCRIPT, "singles_output_path", return_value=out_path),
                mock.patch.object(SCRIPT, "event"),
                mock.patch.object(SCRIPT, "kv"),
                mock.patch.object(SCRIPT, "fit_card_to_cell", wraps=SCRIPT.fit_card_to_cell) as fit_mock,
            ):
                result = SCRIPT.process_singles_batch(singles, args)

            self.assertEqual(result, out_path)
            self.assertTrue(out_path.exists())
            self.assertEqual(len(archived_inputs), 7)
            self.assertEqual(fit_mock.call_count, 7)


class ProcessPdfTests(unittest.TestCase):
    def test_process_pdf_writes_output_and_archives(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            args = base_args(tmp, dpi=72)
            input_pdf = tmp / "input.pdf"
            input_pdf.write_bytes(b"x")
            output_pdf = tmp / "processed" / "out.pdf"

            def fake_render_pdf_to_pngs(_input_pdf, _dpi, workdir):
                page = workdir / "page-1.png"
                Image.new("RGB", (300, 200), "white").save(page)
                return [page]

            archived = []

            with (
                mock.patch.object(SCRIPT, "render_pdf_to_pngs", side_effect=fake_render_pdf_to_pngs),
                mock.patch.object(SCRIPT, "archive_input_pdf", side_effect=lambda p, _a: archived.append(p)),
                mock.patch.object(SCRIPT, "event"),
                mock.patch.object(SCRIPT, "kv"),
            ):
                result = SCRIPT.process_pdf(input_pdf, args, output_pdf=output_pdf)

            self.assertEqual(result, output_pdf.resolve())
            self.assertTrue(output_pdf.exists())
            self.assertEqual(archived, [input_pdf.resolve()])


class RunScriptTests(unittest.TestCase):
    def test_run_sh_has_valid_bash_syntax(self):
        proc = subprocess.run(
            ["bash", "-n", str(RUN_SH_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)


if __name__ == "__main__":
    unittest.main()
