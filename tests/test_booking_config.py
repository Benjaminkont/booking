import contextlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import booking_config


class BookingConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.guide = self.root / "USAGE.md"
        self.guide.write_text("# 帮助测试\n\nbooking-run --dry-run\n\n```python\nRUN_BEFORE_HOURS = 6\n```\n", encoding="utf-8")
        # Help must not import or execute even a syntactically invalid config.
        (self.root / "config.py").write_text("SECRET_SHOULD_NOT_APPEAR = invalid Python !!!\n", encoding="utf-8")

    def test_no_arguments_still_opens_nano(self):
        with patch.object(booking_config.subprocess, "call", return_value=0) as editor:
            self.assertEqual(booking_config.main([], root=self.root), 0)
            editor.assert_called_once_with(["nano", str(self.root / "config.py")])

    def test_editor_exit_code_is_preserved(self):
        with patch.object(booking_config.subprocess, "call", return_value=7):
            self.assertEqual(booking_config.main([], root=self.root), 7)

    def test_help_is_rendered_without_opening_editor_or_reading_config(self):
        before = (self.root / "config.py").read_bytes()
        for flag in ("help", "-h", "--help"):
            output = io.StringIO()
            with self.subTest(flag=flag), contextlib.redirect_stdout(output), patch.object(booking_config.subprocess, "call") as editor:
                self.assertEqual(booking_config.main([flag], root=self.root), 0)
                editor.assert_not_called()
            self.assertIn("帮助测试", output.getvalue())
            self.assertIn("booking-run --dry-run", output.getvalue())
            self.assertNotIn("SECRET_SHOULD_NOT_APPEAR", output.getvalue())
            self.assertNotIn("```", output.getvalue())
        self.assertEqual(before, (self.root / "config.py").read_bytes())
        self.assertFalse((self.root / ".booking-service").exists())

    def test_help_uses_rich_markdown_renderer(self):
        from rich.markdown import Markdown
        with patch("rich.console.Console") as console:
            self.assertEqual(booking_config.main(["help"], root=self.root), 0)
        rendered = console.return_value.print.call_args.args[0]
        self.assertIsInstance(rendered, Markdown)
        self.assertEqual(rendered.markup, self.guide.read_text(encoding="utf-8"))

    def test_help_does_not_require_a_config_file(self):
        (self.root / "config.py").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(booking_config.main(["help"], root=self.root), 0)

    def test_unknown_or_extra_arguments_fail_without_editor(self):
        for arguments in (["unknown"], ["help", "extra"], ["config.py"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), patch.object(booking_config.subprocess, "call") as editor:
                self.assertEqual(booking_config.main(arguments, root=self.root), 2)
                editor.assert_not_called()

    def test_missing_guide_is_clear_and_has_no_traceback(self):
        self.guide.unlink()
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(booking_config.main(["help"], root=self.root), 1)
        self.assertIn("USAGE.md", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_invalid_utf8_guide_has_no_traceback(self):
        self.guide.write_bytes(b"\xff")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(booking_config.main(["help"], root=self.root), 1)

    def test_missing_config_does_not_create_empty_config(self):
        (self.root / "config.py").unlink()
        with contextlib.redirect_stderr(io.StringIO()), patch.object(booking_config.subprocess, "call") as editor:
            self.assertEqual(booking_config.main([], root=self.root), 1)
            editor.assert_not_called()

    def test_missing_editor_is_clear(self):
        with contextlib.redirect_stderr(io.StringIO()), patch.object(booking_config.subprocess, "call", side_effect=FileNotFoundError):
            self.assertEqual(booking_config.main([], root=self.root), 1)

    def test_missing_rich_explains_dependency_install(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), patch.dict(sys.modules, {"rich.console": None}):
            self.assertEqual(booking_config.main(["help"], root=self.root), 1)
        self.assertIn("pip install -r requirements.txt", errors.getvalue())
        self.assertNotIn("Traceback", errors.getvalue())

    def test_real_cli_from_other_directory_has_no_booking_side_effects(self):
        shutil.copyfile(booking_config.__file__, self.root / "booking_config.py")
        result = subprocess.run(
            [sys.executable, "-B", str(self.root / "booking_config.py"), "help"],
            cwd=self.root.parent, text=True, capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"}, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("帮助测试", result.stdout)
        self.assertNotIn("SECRET_SHOULD_NOT_APPEAR", result.stdout + result.stderr)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["USAGE.md", "booking_config.py", "config.py"])

    def test_real_guide_keeps_full_safety_instructions_at_80_columns(self):
        from rich.console import Console
        from rich.markdown import Markdown
        output = io.StringIO()
        guide = (Path(booking_config.__file__).resolve().parent / "USAGE.md").read_text(encoding="utf-8")
        Console(file=output, width=80, color_system=None).print(Markdown(guide))
        compact = re.sub(r"\s+", "", output.getvalue())
        self.assertIn("不要立刻反复重跑", compact)
        self.assertIn("不读账号密码、不登录、不预约", compact)


if __name__ == "__main__":
    unittest.main()
