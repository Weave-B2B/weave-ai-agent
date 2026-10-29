"""Behavioral tests for portable, non-overwriting workflow installation."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap.py"
START = "<!-- manager-dev-workflow:start -->"
END = "<!-- manager-dev-workflow:end -->"


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "bundle"
        self.target = self.base / "project"
        self.target.mkdir()
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "assets/roles").mkdir(parents=True)
        (self.source / "scripts/bootstrap.py").write_bytes(BOOTSTRAP.read_bytes())
        (self.source / "SKILL.md").write_text("---\nname: manager-dev-workflow\ndescription: Test fixture.\n---\n", encoding="utf-8")
        (self.source / "assets/project.example.json").write_text('{"project": "example"}\n', encoding="utf-8")
        (self.source / "assets/AGENTS-section.md").write_text("Workflow instructions.\n", encoding="utf-8")
        (self.source / "assets/roles/qa.toml").write_text(
            'name = "qa"\ndescription = "QA reviewer"\ndeveloper_instructions = "Report findings."\n',
            encoding="utf-8",
        )
        cache = self.source / "scripts/__pycache__"
        cache.mkdir()
        (cache / "bootstrap.cpython-311.pyc").write_bytes(b"ignored")

    def run_cli(self, *, target=None, dry_run=False):
        args = [sys.executable, str(self.source / "scripts/bootstrap.py"), "--target", str(target or self.target)]
        if dry_run:
            args.append("--dry-run")
        return subprocess.run(args, capture_output=True, text=True, check=False)

    def snapshot(self, path=None):
        root = path or self.target
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.is_symlink()}

    def test_fresh_install_and_repeat_are_idempotent(self):
        original = "# Team conventions\r\nKeep existing rules.\r\n"
        (self.target / "AGENTS.md").write_bytes(original.encode())
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / ".agents/skills/manager-dev-workflow/SKILL.md").is_file())
        self.assertTrue((self.target / ".codex/agents/qa.toml").is_file())
        self.assertTrue((self.target / "docs/dev-workflow/project.json").is_file())
        self.assertFalse((self.target / ".agents/skills/manager-dev-workflow/scripts/__pycache__").exists())
        agents = (self.target / "AGENTS.md").read_bytes().decode()
        self.assertTrue(agents.startswith(original))
        self.assertEqual(agents.count(START), 1)
        installed = self.snapshot()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), installed)
        self.assertIn("0 file(s)", result.stdout)

    def test_existing_custom_project_profile_is_preserved(self):
        profile = self.target / "docs/dev-workflow/project.json"
        profile.parent.mkdir(parents=True)
        custom = b'{"project":"custom", "notion_sources":["private-link"]}\n'
        profile.write_bytes(custom)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(profile.read_bytes(), custom)

    def test_role_collision_causes_no_partial_writes(self):
        existing = self.target / ".codex/agents/qa.toml"
        existing.parent.mkdir(parents=True)
        existing.write_text("custom role", encoding="utf-8")
        before = self.snapshot()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.target / ".agents").exists())

    def test_managed_block_collision_causes_no_partial_writes(self):
        (self.target / "AGENTS.md").write_text(f"# Existing\n{START}\nCustom workflow.\n{END}\n", encoding="utf-8")
        before = self.snapshot()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.target / ".agents").exists())

    def test_malformed_managed_block_is_rejected(self):
        (self.target / "AGENTS.md").write_text(f"# Existing\n{START}\n", encoding="utf-8")
        before = self.snapshot()
        self.assertNotEqual(self.run_cli().returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_changes_nothing(self):
        before = list(self.target.iterdir())
        result = self.run_cli(dry_run=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would write", result.stdout)
        self.assertEqual(list(self.target.iterdir()), before)

    def test_symlinked_parent_cannot_write_outside_target(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.target / ".codex").symlink_to(outside, target_is_directory=True)
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Symlink", result.stderr)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.target / ".agents").exists())

    def test_symlinked_agents_file_cannot_overwrite_outside_file(self):
        outside = self.base / "outside.md"
        outside.write_text("Untouched", encoding="utf-8")
        (self.target / "AGENTS.md").symlink_to(outside)
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outside.read_text(encoding="utf-8"), "Untouched")
        self.assertFalse((self.target / ".agents").exists())

    def test_recursive_target_inside_bundle_is_rejected(self):
        recursive = self.source / "nested-project"
        recursive.mkdir()
        result = self.run_cli(target=recursive)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Recursive", result.stderr)
        self.assertEqual(list(recursive.iterdir()), [])

    def test_same_source_and_installed_skill_skips_copy(self):
        installed = self.target / ".agents/skills/manager-dev-workflow"
        installed.parent.mkdir(parents=True)
        self.source.rename(installed)
        self.source = installed
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / ".codex/agents/qa.toml").is_file())
        self.assertEqual(self.run_cli().returncode, 0)

    def test_missing_target_is_not_created(self):
        missing = self.base / "does-not-exist"
        self.assertNotEqual(self.run_cli(target=missing).returncode, 0)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
