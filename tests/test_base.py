from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from agents.base import collect_code_files, extract_fenced_section, parse_file_blocks, write_files


class BaseHelpersTests(unittest.TestCase):
    def test_parse_file_blocks_strips_optional_code_fence(self):
        text = """=== FILE: src/app.py ===
```python
print("hello")
```
=== END FILE ===
"""

        self.assertEqual(parse_file_blocks(text), [("src/app.py", 'print("hello")')])

    def test_extract_fenced_section_requires_both_markers(self):
        self.assertEqual(
            extract_fenced_section("=== PLAN.md ===\nbody\n=== END PLAN.md ===", "PLAN.md"),
            "body",
        )
        self.assertEqual(extract_fenced_section("body only", "PLAN.md"), "")
        self.assertEqual(extract_fenced_section("=== PLAN.md ===\nbody", "PLAN.md"), "")

    def test_write_files_refuses_path_traversal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            written = write_files(
                root,
                [
                    ("safe.txt", "ok"),
                    ("../escape.txt", "bad"),
                ],
            )

            self.assertEqual([path.name for path in written], ["safe.txt"])
            self.assertTrue((root / "safe.txt").exists())
            self.assertFalse((root.parent / "escape.txt").exists())

    def test_collect_code_files_excludes_credential_filenames(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
            for name in [
                ".env",
                ".env.local",
                ".npmrc",
                "id_rsa",
                "service.pem",
                "deploy-key.txt",
                "credentials.yaml",
                "api_keys.py",
                "service-account.json",
                "access-token.json",
                "client_secrets.py",
            ]:
                (root / name).write_text("SECRET=value\n", encoding="utf-8")
            (root / "secrets").mkdir()
            (root / "secrets" / "settings.py").write_text("TOKEN='bad'\n", encoding="utf-8")
            for dirname in ["api_keys", "service-account", "workspace", "runs"]:
                (root / dirname).mkdir()
                (root / dirname / "settings.py").write_text("TOKEN='bad'\n", encoding="utf-8")

            files = {path.relative_to(root).as_posix() for path in collect_code_files(root)}

        self.assertEqual(files, {"src/app.py"})


if __name__ == "__main__":
    unittest.main()
