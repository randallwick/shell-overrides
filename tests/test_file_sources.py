import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GUARD = "token_provider_shell_overrides_authorize"


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
token_completion_scope() { claimed=1; }
token_completion_file() {
  # Mirror the public helper/guard calling convention, without loading a
  # parser or reading the source document in this plugin's test suite.
  [[ $claimed == 1 && $# == 7 ]] || exit 91
  "$5" "$6" "$7" "$2" "$3" "$4" || exit 92
  printf '%s\\0' "$@"
  claimed=0
}
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

    def test_matching_rule_delegates_exact_source_policy_and_identity(self):
        self.assertEqual(
            self.fields(
                self.shell("token_provider_shell_overrides_context wo probe --profile separate read")
            ),
            ["wo", "ini", "section-names", "~/accounts.ini", GUARD, "probe", "--profile"],
        )
        for context in [
            "wo other --profile equals read",
            "wo probe --profile separate write",
            "wo probe --profile equals read --skip",
            "re probe --value separate read",
        ]:
            with self.subTest(context):
                self.assertEqual(
                    self.fields(self.shell("token_provider_shell_overrides_context " + context)), []
                )
        self.assertEqual(
            self.fields(self.shell("token_provider_shell_overrides_context re probe --value equals read")),
            [
                "re",
                "json",
                "leaf-values",
                "./settings.json",
                GUARD,
                "probe",
                "--value",
                "re",
                "yaml",
                "leaf-values",
                "./other.yaml",
                GUARD,
                "probe",
                "--value",
            ],
        )

    def test_option_consumed_as_value_is_outside_scope(self):
        rules = self.manifest.read_text().replace(
            "      completions:",
            """
      defaults:
        - append: [--label, normal]
          forms: [separate]
      completions:""",
        )
        self.manifest.write_text(rules)
        for form in ("separate", "equals"):
            probe = f"token_provider_shell_overrides_context wo probe --profile {form} read"
            with self.subTest(form=form):
                for prefix in ("--label", "--profile", "--", "--label name --"):
                    self.assertEqual(self.fields(self.shell(probe + " " + prefix)), [])
                # A completed option/value pair leaves the next word eligible.
                # A flag-shaped value, itself known as a flag, remains a value.
                for prefix in ("--label name", "--label --profile", "--profile --label"):
                    self.assertEqual(self.fields(self.shell(probe + " " + prefix))[::7], ["wo"])

    def test_authorization_follows_live_manifest(self):
        allowed = f"{GUARD} probe --profile ini section-names '~/accounts.ini'"
        for request in [
            allowed.replace("section-names", "leaf-values"),
            allowed.replace("probe", "other"),
            allowed.replace("--profile", "--account"),
            allowed.replace(" ini ", " toml "),
            allowed.replace("accounts.ini", "other.ini"),
            allowed + " extra",
        ]:
            with self.subTest(request):
                result = self.shell(request)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((result.stdout, result.stderr), (b"", b""))
        self.assertEqual(self.shell(allowed).returncode, 0)

        # A valid revision changes the policy. A malformed edit then retains
        # the latest valid revision; removing and reloading finally revokes it.
        self.manifest.with_suffix(".next").write_text(
            self.manifest.read_text().replace("--profile", "--account")
        )
        result = self.shell(f"""
mv "$SHELL_OVERRIDES_MANIFEST_DIR/cli.next" "$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml"
shell-overrides reload
{allowed} && exit 93
echo 'version: [' > "$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml"
shell-overrides reload
{allowed.replace("--profile", "--account")} || exit 94
rm "$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml"
shell-overrides reload
{allowed.replace("--profile", "--account")} && exit 95
exit 0
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"cli.yaml", result.stderr)
        for path in (self.home / "cache").rglob("*"):
            if path.is_file():
                self.assertNotIn(b"NEVER_DISPLAY", path.read_bytes())
