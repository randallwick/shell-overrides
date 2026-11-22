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
          source: {file: '~/accounts', select: section-names}
        - option: --value
          forms: [equals]
          source: {file: ./settings.json, format: json, select: leaf-values}
        - option: --value
          forms: [equals]
          source: {file: ./other.yaml, format: yaml, select: leaf-values}
""")
        (self.home / "accounts").write_text("[work-read]\nsecret = NEVER_DISPLAY\n")
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
  [[ $claimed == 1 ]] || exit 91
  printf '%s\\0' "$@"
  claimed=0
  if (( $# == 5 || $# == 9 )); then
    [[ $1 == --exact ]] || exit 91
    shift
  fi
  (( $# == 4 )) && return 0
  [[ $# == 8 && $2 == "$8" ]] || exit 91
  local resolved=$2
  # Our fixture decoder selects INI for the extensionless accounts source.
  [[ $resolved == auto ]] && resolved=ini
  "$5" "$6" "$7" "$8" "$resolved" "$3" "$4" || exit 92
}
token_completion_file_literal() { token_completion_file "$@"; }
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
            ["wo", "auto", "section-names", "~/accounts"],
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
                "re",
                "yaml",
                "leaf-values",
                "./other.yaml",
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
                    self.assertEqual(self.fields(self.shell(probe + " " + prefix))[::4], ["wo"])

    def test_markerless_and_physical_positions(self):
        self.manifest.write_text(
            self.manifest.read_text().replace(
                "        - option: --profile",
                """        - position: 2
          markerless: {empty: native}
          source: {format: json, select: keys, file: ./settings.json}
        - position: 3
          markerless: {empty: all}
          source: {format: json, select: keys, file: ./settings.json}
        - option: --profile
          markerless: {empty: all}""",
            )
        )
        hook = "token_provider_shell_overrides_argument_context "
        for arguments, selector, literal in [
            ("literal '' probe --profile separate read", "--profile", True),
            ("literal wo probe --profile equals read", "--profile", True),
            ("token '' probe '' positional read", "@2", False),
            ("literal dep probe '' positional read", "@2", True),
            ("literal '' probe '' positional read --verbose", "@3", True),
            ("literal -test probe '' positional read --", "@3", True),
        ]:
            with self.subTest(arguments):
                row = self.fields(self.shell(hook + arguments))
                if literal:
                    self.assertEqual(row[4:7], [GUARD + "_literal", "probe", selector])
                else:
                    self.assertEqual(row, ["", "json", "keys", "./settings.json"])
        for arguments, claim in [
            ("literal '' probe '' positional read", 1),
            ("literal wo probe --value equals read", 1),
            ("literal wo probe --profile separate read --", 0),
            ("literal -test probe '' positional read", 0),
            ("literal wo probe '' positional read --profile", 0),
            ("literal wo probe '' positional read --skip", 0),
            ("literal wo probe '' positional write", 0),
        ]:
            with self.subTest(arguments):
                run = self.shell(hook + arguments + '; print -rn -- "${claimed:-0}"')
                self.assertEqual((run.stdout, run.stderr), (str(claim).encode(), b""))

    def test_markerless_authorization_revoked_while_token_mode_remains(self):
        original = self.manifest.read_text()
        self.manifest.with_suffix(".next").write_text(original)
        self.manifest.write_text(
            original.replace(
                "        - option: --profile",
                "        - option: --profile\n          markerless: {empty: native}",
            )
        )
        args = "probe --profile auto ini section-names '~/accounts'"
        run = self.shell(f"""
{GUARD}_literal {args} || exit 93
mv "$SHELL_OVERRIDES_MANIFEST_DIR/cli.next" "$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml"
shell-overrides reload
{GUARD}_literal {args} && exit 94
{GUARD} {args}
""")
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_rejects_invalid_selectors_and_markerless_configuration(self):
        original = self.manifest.read_text()
        for selector in (
            "position: false",
            "position: 0",
            "position: -1",
            "position: '2'",
            "position: 2\n          forms: [equals]",
            "option: --profile\n          position: 2",
            "markerless: {empty: all}",
            "option: --profile\n          markerless: {}",
            "option: --profile\n          markerless: {empty: unsupported}",
        ):
            with self.subTest(selector):
                self.manifest.write_text(original.replace("option: --profile", selector))
                run = self.shell("token_provider_shell_overrides_context wo probe --profile equals read")
                self.assertEqual(self.fields(run), [])
                self.assertIn(b"cli.yaml", run.stderr)

    def test_deferred_settings_compose_and_leave_literal_guards_intact(self):
        original = self.manifest.read_text().replace(
            "        - option: --profile",
            "        - option: --profile\n          markerless: {empty: all}",
        )
        hook = "token_provider_shell_overrides_argument_context"
        standard = ["wo", "auto", "section-names", "~/accounts"]
        identity = ["probe", "--profile", "auto"]
        for options, guarded, exact in [
            ("{}", False, False),
            ("{guard: false, preserve_trailing_newlines: false}", False, False),
            ("{guard: true}", True, False),
            ("{preserve_trailing_newlines: true}", False, True),
            ("{guard: true, preserve_trailing_newlines: true}", True, True),
        ]:
            with self.subTest(options):
                self.manifest.write_text(
                    original.replace(
                        "          markerless: {empty: all}",
                        "          markerless: {empty: all}\n          deferred: " + options,
                    )
                )
                token = self.fields(self.shell(f"{hook} token wo probe --profile equals read"))
                expected = (["--exact"] if exact else []) + standard
                self.assertEqual(token, expected + ([GUARD, *identity] if guarded else []))
                literal = self.fields(self.shell(f"{hook} literal wo probe --profile equals read"))
                self.assertEqual(literal, standard + [GUARD + "_literal", *identity])

    def test_deferred_settings_require_a_mapping_of_known_booleans(self):
        original = self.manifest.read_text()
        for value in (
            "null",
            "true",
            "{guard: 1}",
            "{guard: 'false'}",
            "{preserve_trailing_newlines: null}",
            "{exact: true}",
        ):
            with self.subTest(value):
                self.manifest.write_text(
                    original.replace(
                        "        - option: --profile",
                        "        - option: --profile\n          deferred: " + value,
                    )
                )
                run = self.shell("token_provider_shell_overrides_context wo probe --profile equals read")
                self.assertEqual(self.fields(run), [])
                self.assertIn(b"cli.yaml", run.stderr)

    def test_format_mode_change_revokes_both_new_and_legacy_history(self):
        # Both records would use the same concrete parser. A manifest edit
        # still changes which policy the saved command requested.
        auto = f"{GUARD} probe --profile auto ini section-names '~/accounts'"
        explicit = auto.replace(" auto ini ", " ini ini ")
        legacy = auto.replace(" auto ini ", " ini ")
        self.manifest.with_suffix(".next").write_text(
            self.manifest.read_text().replace("file: '~/accounts'", "file: '~/accounts', format: ini")
        )
        run = self.shell(f"""
{auto} || exit 93
{explicit} && exit 94
{legacy} && exit 95
mv "$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml" "$SHELL_OVERRIDES_MANIFEST_DIR/cli.prev"
mv "$SHELL_OVERRIDES_MANIFEST_DIR/cli.next" "$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml"
shell-overrides reload
{auto} && exit 96
{explicit} || exit 97
{legacy} || exit 98
mv "$SHELL_OVERRIDES_MANIFEST_DIR/cli.prev" "$SHELL_OVERRIDES_MANIFEST_DIR/cli.yaml"
shell-overrides reload
{legacy} && exit 99
{explicit} && exit 100
{auto}
""")
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_rejects_unsupported_format_and_policy_pairs(self):
        original = self.manifest.read_text()
        for field in ("format: go", "format: yaml", "format: null"):
            with self.subTest(field):
                self.manifest.write_text(
                    original.replace("file: '~/accounts'", f"file: '~/accounts', {field}")
                )
                run = self.shell("token_provider_shell_overrides_context wo probe --profile separate read")
                self.assertEqual(self.fields(run), [])
                self.assertIn(b"cli.yaml", run.stderr)

    def test_authorization_follows_live_manifest(self):
        allowed = f"{GUARD} probe --profile auto ini section-names '~/accounts'"
        for request in [
            allowed.replace("section-names", "leaf-values"),
            allowed.replace("probe", "other"),
            allowed.replace("--profile", "--account"),
            allowed.replace(" ini ", " toml "),
            allowed.replace(" auto ini ", " ini ini "),
            allowed.replace(" auto ini ", " auto plugin "),
            allowed.replace("accounts", "other.ini"),
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
