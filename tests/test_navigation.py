"""Navigation commands run in the invoking zsh and read the current directory tree."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "shell-overrides.plugin.zsh"
MANIFEST = """version: 1
commands:
  jump:
    navigate:
      root: ~/work
      env: WORK_ROOT
      markerless: {empty: all}
"""


class Navigation(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.manifest = self.home / "manifests" / "nav.yaml"
        self.manifest.parent.mkdir()
        self.manifest.write_text(MANIFEST)
        self.root = self.home / "work"
        self.root.mkdir()
        self.env = {**os.environ, "HOME": str(self.home), "PLUGIN": str(PLUGIN),
                    "SHELL_OVERRIDES_MANIFEST_DIR": str(self.manifest.parent),
                    "SHELL_OVERRIDES_PYTHON": sys.executable, "XDG_CACHE_HOME": str(self.home / "cache")}

    def run_zsh(self, script, **env):
        return subprocess.run(["zsh", "-dfc", 'source "$PLUGIN" || exit 99\n' + script],
                              env={**self.env, **env}, cwd=self.home, capture_output=True, timeout=10)

    def test_exact_and_fuzzy(self):
        for name in ("dev", "dev-a", "client api", ".hidden", "-dash", "cash $(touch bad);", "trailing\n"):
            (self.root / name).mkdir()
        for query, name in [("dev", "dev"), ("capi", "client api"), ("client api", "client api"),
                            (".hidden", ".hidden"), ("-dash", "-dash"),
                            ("cash $(touch bad);", "cash $(touch bad);"), ("trailing\n", "trailing\n")]:
            with self.subTest(query=query):
                run = self.run_zsh('jump "$QUERY" && print -rn -- "$PWD"', QUERY=query)
                self.assertEqual(run.stdout, os.fsencode(self.root / name), run.stderr)
        self.assertFalse((self.home / "bad").exists())
        run = self.run_zsh('jump capi && print -rn -- "$PWD"', FZF_DEFAULT_OPTS="--print-query")
        self.assertEqual(run.stdout, os.fsencode(self.root / "client api"))
        run = self.run_zsh('jump client api && print -rn -- "$PWD"')
        self.assertEqual(run.stdout, os.fsencode(self.root / "client api"))

    def test_dynamic_root_symlinks_and_no_match(self):
        other = self.home / "new root"
        other.mkdir()
        link = other / "external"
        link.symlink_to(self.root)
        (other / "file").touch()
        run = self.run_zsh('jump xtnl && print -rn -- "$PWD"', WORK_ROOT=str(other))
        self.assertEqual(run.stdout, os.fsencode(link), run.stderr)
        run = self.run_zsh('jump file; print -r -- "$? $PWD"', WORK_ROOT=str(other))
        self.assertEqual(run.stdout.decode().strip(), f"1 {self.home}")
        run = self.run_zsh('mkdir "$HOME/work/new"; jump && print -rn -- "$PWD"', WORK_ROOT="")
        self.assertEqual(run.stdout, os.fsencode(self.root / "new"), run.stderr)

    def test_completion_scope_and_reload(self):
        self.manifest.write_text(MANIFEST.replace("WORK_ROOT", "cmd"))
        run = self.run_zsh("""
token_completion_scope() { print scope; }
token_completion_from() { print -r -- "$#:$1:$2:$3"; }
query() { token_provider_shell_overrides_argument_context "$@"; }
query literal "" jump "" positional
query literal wo jump "" positional prior
query literal wo other "" positional
query token wo jump "" positional
rm "$SHELL_OVERRIDES_MANIFEST_DIR/nav.yaml"
shell-overrides reload
(( ! $+functions[jump] ))
""", cmd=str(self.root / "override"))
        self.assertEqual(run.stdout.decode().splitlines(), [f"3:directory::{self.root}/override"], run.stderr)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_execution_and_completion_are_independent(self):
        (self.root / "client api").mkdir()
        run = self.run_zsh("""
token_completion_directory_matches() { print -rN -- missing; }
token_completion_from() { print unexpected; }
jump capi && print -rn -- "$PWD"
""")
        self.assertEqual(run.stdout, os.fsencode(self.root / "client api"), run.stderr)
        run = self.run_zsh("""
token_completion_scope() { print unexpected; }
token_completion_from() { return 1; }
token_provider_shell_overrides_argument_context literal "" jump "" positional
""")
        self.assertEqual(run.stdout, b"", run.stderr)

    def test_malformed_edit_preserves_command(self):
        (self.root / "ok").mkdir()
        self.run_zsh("true").check_returncode()
        self.manifest.write_text(MANIFEST.replace("WORK_ROOT", "'x[$(touch bad)]'"))
        run = self.run_zsh('jump ok && print -rn -- "$PWD"')
        self.assertIn(b"navigate.env", run.stderr)
        self.assertEqual(run.stdout, os.fsencode(self.root / "ok"))
        self.assertFalse((self.home / "bad").exists())
