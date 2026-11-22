import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "file_source.py"


class Sources(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.manifest = self.home / "manifests/cli.yaml"
        self.manifest.parent.mkdir()
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_CACHE_HOME": str(self.home / "cache"),
            "SHELL_OVERRIDES_MANIFEST_DIR": str(self.manifest.parent),
            "SHELL_OVERRIDES_PYTHON": sys.executable,
            "PLUGIN": str(ROOT / "shell-overrides.plugin.zsh"),
        }
        self.manifest.write_text("""version: 1
commands:
  probe:
    - prefix: [read]
      exclude: [{names: [--skip]}]
      completions:
        - option: --profile
          source: {file: '~/accounts.ini', format: ini, select: section-names}
        - option: --value
          forms: [equals]
          source: {file: ./settings.json, format: json, select: leaf-values}
        - option: --value
          forms: [equals]
          source: {file: ./other.yaml, format: yaml, select: leaf-values}
""")
        (self.home / "accounts.ini").write_text("[work-read]\nsecret = NEVER_DISPLAY\n")
        (self.home / "other.yaml").write_text("region: west")
        (self.home / "settings.json").write_text('{"region":"east"}')

    def shell(self, script):
        return subprocess.run(
            [
                "zsh",
                "-dfc",
                """
source "$PLUGIN" || exit 90
token_completion_scope() { :; }
token_completion_offer() { printf '%s\\0' "$@"; }
"""
                + script,
            ],
            env=self.env,
            cwd=self.home,
            capture_output=True,
        )

    def fields(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode().split("\0")[:-1]

    def test_policy_and_reload(self):
        self.assertEqual(
            self.fields(
                self.shell("token_provider_shell_overrides_context wo probe --profile separate read")
            )[:2],
            ["work-read", "~/accounts.ini"],
        )
        for context in [
            "wo other --profile equals read",
            "wo probe --profile separate write",
            "wo probe --profile equals read --skip",
            "re probe --value separate read",
        ]:
            self.assertFalse(
                self.fields(self.shell("token_provider_shell_overrides_context " + context))
            )
        rows = self.fields(
            self.shell("token_provider_shell_overrides_context re probe --value equals read")
        )
        self.assertEqual(rows[1::3], ["./settings.json", "./other.yaml"])
        cmd = rows[2]
        (self.home / "settings.json").write_text(json.dumps({"region": "a \n\n"}))
        result = self.shell("printf '%s' " + cmd)
        self.assertEqual(result.stdout, b"a \n\n")
        result = self.shell(
            "shell-overrides lookup probe --profile ini section-names '~/accounts.ini' work-read.secret"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"NEVER_DISPLAY", result.stdout + result.stderr)
        result = self.shell(
            "rm -- \"$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml\"; shell-overrides reload; printf '<%s>' "
            + cmd
        )
        self.assertEqual(result.stdout, b"<>")
        self.assertIn(b"no longer declared", result.stderr)

    def test_option_consumed_as_value_is_outside_scope(self):
        rules = self.manifest.read_text().replace("      completions:", """
      defaults:
        - append: [--label, normal]
          forms: [separate]
      completions:""")
        self.manifest.write_text(rules)
        for form in ("separate", "equals"):
            probe = f"token_provider_shell_overrides_context wo probe --profile {form} read"
            with self.subTest(form=form):
                for prefix in ("--label", "--profile", "--", "--label name --"):
                    self.assertEqual(self.fields(self.shell(probe + " " + prefix)), [])
                # A completed option/value pair leaves the next word eligible.
                # A flag-shaped value, itself known as a flag, remains a value.
                for prefix in ("--label name", "--label --profile", "--profile --label"):
                    self.assertEqual(self.fields(self.shell(probe + " " + prefix))[::3], ["work-read"])

    def test_authorization_follows_live_manifest(self):
        forbidden = (
            "shell-overrides lookup probe --profile ini leaf-values '~/accounts.ini' work-read.secret"
        )
        result = self.shell(forbidden)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"NEVER_DISPLAY", result.stderr)
        # A new valid revision changes the rule in this shell; an invalid edit
        # retains the latest validated revision. Source contents stay out of cache.
        updated = self.manifest.read_text().replace("--profile", "--account")
        self.manifest.with_suffix(".next").write_text(updated)
        result = self.shell("""
mv "$SHELL_OVERRIDES_MANIFEST_DIR/cli.next" "$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml"
shell-overrides reload
echo 'version: [' > "$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml"
shell-overrides reload
token_provider_shell_overrides_context wo probe --profile equals read
token_provider_shell_overrides_context wo probe --account equals read
""")
        self.assertEqual(self.fields(result)[::3], ["work-read"])
        self.assertIn(b"cli.yaml", result.stderr)
        for path in (self.home / "cache").rglob("*"):
            if path.is_file():
                self.assertNotIn(b"NEVER_DISPLAY", path.read_bytes())

    def helper(self, action, fmt, policy, path):
        return subprocess.run(
            [sys.executable, str(HELPER), action, fmt, policy, str(self.home / "document"), path],
            capture_output=True,
        )

    def test_ini_sections_are_literal(self):
        (self.home / "document").write_text("[DEFAULT]\nregion=west\n[default]\nregion=east\n[work]\n")
        rows = self.fields(self.helper("list", "ini", "section-names", ""))
        self.assertEqual(rows[2::3], ["DEFAULT", "default", "work"])
        self.assertEqual(self.helper("get", "ini", "leaf-values", "DEFAULT.region").stdout, b"west")
        self.assertNotEqual(self.helper("get", "ini", "leaf-values", "work.region").returncode, 0)

    def test_formats_and_component_selection(self):
        for fmt, doc in {
            "ini": "[deploy]\nregion=west\n[dependency]\nregion=east\n",
            "toml": '[deploy]\nregion="west"\n[dependency]\nregion="east"\n',
            "json": '{"deploy":{"region":"west"},"dependency":{"region":"east"}}',
            "yaml": "deploy: {region: west}\ndependency: {region: east}",
        }.items():
            with self.subTest(fmt=fmt):
                (self.home / "document").write_text(doc)
                rows = self.fields(self.helper("list", fmt, "leaf-values", "de.re"))
                self.assertEqual(set(rows[2::3]), {"deploy.re", "dependency.re"})
                self.assertEqual(
                    self.fields(self.helper("list", fmt, "leaf-values", "depl.re")),
                    ["leaf", "deploy.region", "deploy.region"],
                )
                self.assertEqual(self.helper("get", fmt, "leaf-values", "deploy.region").stdout, b"west")
                self.assertEqual(self.helper("get", fmt, "keys", "deploy").stdout, b"deploy")
                self.assertNotEqual(self.helper("get", fmt, "keys", "deploy.region").returncode, 0)

    def test_keys_arrays_empty_missing_and_malformed(self):
        file = self.home / "document"
        file.write_text('{"a.b":[{"x y":""}]}')
        rows = self.fields(self.helper("list", "json", "leaf-values", "a%2Eb.0.x"))
        self.assertEqual(rows, ["leaf", "a%2Eb.0.x%20y", "a%2Eb.0.x%20y"])
        result = self.helper("get", "json", "leaf-values", rows[2])
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
        for bad in ["{}", "secret user text !"]:
            file.write_text(bad)
            result = self.helper("get", "json", "leaf-values", rows[2])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertNotIn(b"secret user text", result.stderr)
