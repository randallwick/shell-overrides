"""Run with a PyYAML-enabled Python and zsh on PATH."""
import codecs
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "shell-overrides.plugin.zsh"
DOC = """version: 1
commands:
  probe:
    - exact: [show]
      defaults: [{append: [--color]}]
    - prefix: [copy]
      exclude: [{names: [--dry-run]}]
      defaults:
        - append: [--format, neutral]
          names: [--format, -f]
          forms: [separate, equals, attached]
        - append: [--verbose]
          names: [--verbose, -v]
          forms: [bare, equals]
tokens:
  CITY: Montréal
  LITERAL: |-
    $HOME 'a'; $(echo unexpected)
    line two
"""


class Plugin(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.config = self.home / "config" / "shell-overrides"
        self.config.mkdir(parents=True)
        self.manifest = self.config / "defaults.yaml"
        self.manifest.write_text(DOC)
        self.cache = self.home / "cache"
        self.env = dict(HOME=str(self.home), XDG_CONFIG_HOME=str(self.config.parent),
                        XDG_CACHE_HOME=str(self.cache), PATH=f"{self.home}:{os.environ['PATH']}",
                        PLUGIN=str(PLUGIN), SHELL_OVERRIDES_PYTHON=sys.executable)
        probe = self.home / "probe"
        probe.write_text(f'#!{sys.executable}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\nsys.exit(23)\n')
        probe.chmod(0o700)

    def shell(self, script, pre="", **env):
        return subprocess.run(["zsh", "-dfc", 'path=("$HOME" $path)\n'+pre+'\nsource "$PLUGIN" || exit 99\n'+script],
                              env={**self.env, **env}, cwd=self.home, text=True, capture_output=True, timeout=10)

    def assert_argv(self, script, expected, **env):
        run = self.shell(script, **env)
        self.assertEqual(run.returncode, 23, run.stderr)
        self.assertEqual(json.loads(run.stdout), expected)
        return run

    def test_defaults_and_precedence(self):
        for script, expected in [
            ("probe show", ["show", "--color"]),
            ("probe show ''", ["show", ""]),
            ("probe copy", ["copy", "--format", "neutral", "--verbose"]),
            ("probe copy --format=false", ["copy", "--format=false", "--verbose"]),
            ("probe copy -f false -v", ["copy", "-f", "false", "-v"]),
            ("probe copy -f0 --verbose=false", ["copy", "-f0", "--verbose=false"]),
            ("probe copy --format ''", ["copy", "--format", "", "--verbose"]),
            ("probe copy --dry-run", ["copy", "--dry-run"]),
            ("probe copy --formatting", ["copy", "--formatting", "--format", "neutral", "--verbose"]),
            ("probe copy -- -v", ["copy", "--format", "neutral", "--verbose", "--", "-v"]),
            ("probe '$(touch marker)' ';' '*' ''", ["$(touch marker)", ";", "*", ""]),
            ("SHELL_OVERRIDE=0 probe show", ["show"]),
            ("SHELL_OVERRIDE=false probe show", ["show"]),
        ]:
            with self.subTest(script):
                self.assert_argv(script, expected)
        self.assertFalse((self.home/"marker").exists())
        result = self.shell('probe show | cat > capture; probe copy -- -f1 | cat')
        self.assertEqual(json.loads((self.home/"capture").read_text()), ["show", "--color"])
        self.assertEqual(json.loads(result.stdout), ["copy", "--format", "neutral", "--verbose", "--", "-f1"])

    def test_exclusions_and_aliases_can_share_spelling(self):
        self.manifest.write_text("""version: 1
commands:
  probe:
    - prefix: [upload]
      exclude: [{names: [--silent]}]
      defaults:
        - append: [--silent]
        - append: [--pretty]
    - prefix: [upload]
      defaults: [{append: [--fallback]}]
""")
        self.assert_argv("probe upload --silent", ["upload", "--silent", "--fallback"])
        self.assert_argv("probe upload", ["upload", "--silent", "--pretty"])

    def test_wrapped_program_sees_callers_environment(self):
        (self.home / "probe").write_text(
            f'#!{sys.executable}\nimport os\nprint(os.environ["target"], os.environ["arg"])\n'
        )
        run = self.shell("export target=destination arg=original; probe show")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, "destination original\n")

    def test_readme_quick_start(self):
        self.manifest.write_text("version: 1\ncommands: {uname: [{exact: [], defaults: [{append: [-sr]}]}]}")
        host = self.shell("uname")
        self.assertEqual(host.returncode, 0, host.stderr)
        self.assertEqual(host.stdout, self.shell("command uname -sr").stdout)
        self.assertEqual(self.shell("uname -m").stdout, self.shell("command uname -m").stdout)

    def test_binary_warm_and_reload(self):
        for encoding, bom in [("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
                              ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE)]:
            with self.subTest(encoding=encoding, bom=bom):
                self.manifest.write_bytes(bom + DOC.encode(encoding))
                self.assert_argv("probe show", ["show", "--color"])
                self.assert_argv("probe show", ["show", "--color"], SHELL_OVERRIDES_PYTHON="false")
                changed = self.home / "changed"
                changed.write_bytes(bom + DOC.replace("--color", "--quiet").encode(encoding))
                self.assert_argv('mv changed "$XDG_CONFIG_HOME/shell-overrides/defaults.yaml"; '
                                 'shell-overrides reload; probe show', ["show", "--quiet"])
                self.assert_argv("probe show", ["show", "--quiet"], SHELL_OVERRIDES_PYTHON="false")

    def test_fresh_in_parallel_symlink_and_deletion(self):
        with ThreadPoolExecutor(8) as pool:
            for run in pool.map(self.shell, ["probe show"]*16):
                self.assertEqual(json.loads(run.stdout), ["show", "--color"], run.stderr)
        self.assertEqual(next(self.cache.rglob("warm")).stat().st_mode & 0o777, 0o600)
        saved = self.home/"generation-1"
        self.manifest.rename(saved)
        self.manifest.symlink_to(saved)
        replacement = self.home/"generation-2"
        replacement.write_text(DOC.replace("--color", "--new"))
        self.assert_argv('ln -snf "$HOME/generation-2" "$XDG_CONFIG_HOME/shell-overrides/defaults.yaml"; '
                         'shell-overrides reload; probe show', ["show", "--new"])
        other = self.config/"other.yml"
        other.write_text("version: 1\ncommands: {other: [{exact: [], defaults: [{append: [--quiet]}]}]}\n")
        self.shell("true")
        self.manifest.write_bytes(b"\xff\x01")
        (self.config/"new.yml").write_text("???")
        result = self.shell('unfunction other; rm "$XDG_CONFIG_HOME/shell-overrides/other.yml"; '
                            'shell-overrides reload; (( $+functions[other] )) && exit 4; probe show')
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["show", "--new"])
        self.assertIn("defaults.yaml", result.stderr)
        self.assertIn("new.yml", result.stderr)
        self.assert_argv("probe show", ["show", "--new"], SHELL_OVERRIDES_PYTHON="false")

    def test_provider_api_and_collisions(self):
        result = self.shell("""[[ $token_provider_shell_overrides_version == 1 ]] || exit 81
token_completion_emit() {
  print -rn -- "$1"$'\\0'"$2"$'\\0'
  # Inspect the shell-quoted expression as an argument, as if accepted in ZLE.
  eval "set -- $3"
  print -rn -- "$1"$'\\0'
}
token_provider_shell_overrides_query ''""")
        self.assertEqual(result.stdout.split("\0"), ["CITY", "defaults.yaml", "Montréal", "LITERAL",
                        "defaults.yaml", "$HOME 'a'; $(echo unexpected)\nline two", ""])
        (self.config/"zzz.yaml").write_text(DOC.replace("--color", "--wrong"))
        self.assertIn("already defined", self.assert_argv("probe show", ["show", "--color"]).stderr)

    def test_directory_isolation_and_legacy(self):
        self.assert_argv("probe show", ["show", "--color"])
        second = self.home/"other"
        second.mkdir()
        (second/"alt.yaml").write_text(DOC.replace("--color", "--other"))
        self.assert_argv("probe show", ["show", "--other"], SHELL_OVERRIDES_CONFIG_DIR=str(second))
        self.manifest.write_text("[")
        self.assert_argv("probe show", ["show", "--color"], SHELL_OVERRIDES_CONFIG_DIR=str(second),
                         SHELL_OVERRIDES_MANIFEST_DIR=str(self.config))
        (second/"alt.yaml").write_text("[")
        self.assert_argv("probe show", ["show", "--other"], SHELL_OVERRIDES_MANIFEST_DIR=str(second))

        legacy_dir = self.home/"legacy"
        legacy_dir.mkdir()
        (legacy_dir/"old.yaml").write_text("??")
        state = self.cache/"shell-overrides/state.json"
        state.write_text(json.dumps({"format": 1, "config": str(legacy_dir), "files": {"old.yaml": {"source": DOC}}}))
        self.assert_argv("probe show", ["show", "--color"], SHELL_OVERRIDES_MANIFEST_DIR=str(legacy_dir))

    def test_cache_damage_and_shell_commands(self):
        self.assert_argv("probe show", ["show", "--color"])
        warm = next(self.cache.rglob("warm"))
        warm.write_bytes(warm.read_bytes()[:-17])
        self.assert_argv("probe show", ["show", "--color"])
        warm.unlink()
        next(self.cache.rglob("state.json")).write_text("[]")
        self.assert_argv("probe show", ["show", "--color"])
        result = self.shell("probe; shell-overrides reload; probe", pre="probe() { print owned; }")
        self.assertEqual(result.stdout, "owned\nowned\n")
        result = self.shell("(( ${+functions[probe]} ))", pre="alias probe='echo other'")
        self.assertEqual(result.returncode, 1, result.stderr)
        result = self.shell("probe() { print new-owner; }; source \"$PLUGIN\"; probe")
        self.assertEqual(result.stdout, "new-owner\n")
        warm.write_bytes(warm.read_bytes().replace(b"\0--color\0", b"\0--wrong\0"))
        self.assert_argv("probe show", ["show", "--color"])
        self.assertEqual(self.shell("shell-overrides --help").returncode, 0)
        self.assertNotIn("\x1b", self.shell("shell-overrides --help").stdout)


if __name__ == "__main__":
    unittest.main()
