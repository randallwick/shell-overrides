# shell-overrides

Give a command better defaults in interactive zsh. Put rules in a YAML file;
the wrapper fills in missing arguments and runs the original executable. All
the usual shell features still work, including pipes, redirects and exit codes.

## First rule in a minute

After [installing the plugin](#installation), give bare `uname` a more useful
default: print the kernel name and release together.

```zsh
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/shell-overrides"
cat > "${XDG_CONFIG_HOME:-$HOME/.config}/shell-overrides/kernel.yaml" <<'YAML'
version: 1
commands:
  uname:
    - exact: []
      defaults:
        - append: [-sr]
YAML
shell-overrides reload
uname       # runs uname -sr
uname -m    # prints the machine architecture, as usual
```

`exact` compares the complete argument list. An empty argument is still an
argument. A rule with `exact: []` handles a bare command. To undo the example,
delete `kernel.yaml` and run `shell-overrides reload`.

## Installation

Source installs require zsh, POSIX `cksum`, and Python 3.9+ with PyYAML.
Check with `python3 -c 'import yaml'`. If the Python on your PATH changes
across projects, create a dedicated environment once:

```zsh
python3 -m venv "${XDG_DATA_HOME:-$HOME/.local/share}/shell-overrides/venv"
"${XDG_DATA_HOME:-$HOME/.local/share}/shell-overrides/venv/bin/python" -m pip install PyYAML
```

Set `SHELL_OVERRIDES_PYTHON=${XDG_DATA_HOME:-$HOME/.local/share}/shell-overrides/venv/bin/python`
in `.zshrc` before the plugin loads. Editing a manifest while a project venv is
active will still use the dedicated environment. The Nix package supplies its
own interpreter, PyYAML and checksum utility.

### Manual

```zsh
plugins=${XDG_DATA_HOME:-$HOME/.local/share}/zsh/plugins
mkdir -p "$plugins"
git clone --branch stable https://github.com/randallwick/shell-overrides.git "$plugins/shell-overrides"
```

Source this file in `.zshrc`:

```zsh
source "${XDG_DATA_HOME:-$HOME/.local/share}/zsh/plugins/shell-overrides/shell-overrides.plugin.zsh"
```

### Antidote

Add the following entry to `${ZDOTDIR:-$HOME}/.zsh_plugins.txt` in your existing
Antidote setup. Run `antidote load` or start a fresh shell. [Antidote options](https://antidote.sh/options)

```text
randallwick/shell-overrides branch:stable
```

### Oh My Zsh

Clone into Oh My Zsh's custom plugins directory:

```zsh
git clone --branch stable https://github.com/randallwick/shell-overrides.git \
  "${ZSH_CUSTOM:-$ZSH/custom}/plugins/shell-overrides"
```

In `.zshrc`, add `plugins+=(shell-overrides)` after your `plugins=(...)`
declaration and before sourcing `oh-my-zsh.sh`. Open a new shell.
[Oh My Zsh custom plugins](https://github.com/ohmyzsh/ohmyzsh/wiki/Customization#adding-a-new-plugin)

### NixOS and Home Manager

Declare the source input in your flake:

```nix
inputs.shell-overrides = {
  url = "github:randallwick/shell-overrides/stable";
  flake = false;
};
```

Use your existing package set. For a Home Manager module receiving `inputs`
through `extraSpecialArgs`:

```nix
{ inputs, pkgs, ... }:
let
  plugin = pkgs.callPackage (inputs.shell-overrides + "/package.nix") {};
in {
  programs.zsh.enable = true;
  programs.zsh.initContent = ''
    source ${plugin}/share/shell-overrides/shell-overrides.plugin.zsh
  '';
}
```

In a NixOS module receiving `inputs` through `specialArgs`, use the same
`plugin` expression and put that source line in
`programs.zsh.interactiveShellInit`. After applying your configuration, open a
new shell and verify with `shell-overrides --help`. The same verification works
with any installation method.

### Updates

| Install | Update the plugin |
| --- | --- |
| Manual branch checkout | Run `git pull --ff-only` in the plugin directory. |
| Antidote | Run `antidote update`. |
| Oh My Zsh | Run `git -C "${ZSH_CUSTOM:-$ZSH/custom}/plugins/shell-overrides" pull --ff-only`. |
| Nix | Run `nix flake update shell-overrides` in the consuming flake, review the changed lock, then apply it through your normal configuration workflow. |

Open a new shell after updating. Follow `stable` for released code or
`development` for ongoing work. To stay on one release, select an immutable tag
such as `v0.1.0` at clone time or in the Nix input
URL. A Nix lock records the exact revision even when the URL selects a branch.

## Recipes

### Defaults on a subcommand

This manifest makes regular directory listings include hidden entries.
Explicit `-a` wins. The command receives one copy of the flag in either case.

```yaml
version: 1
commands:
  ls:
    - prefix: []
      defaults:
        - append: [-a]
          names: [-a]
```

```zsh
ls ./src       # argv: ./src -a
ls -a ./src    # argv: -a ./src
ls -- -a       # argv: -a -- -a   (the second -a is a filename)
```

A shell alias or function named `ls` already owns that name. In that case,
try this recipe in a fresh `zsh -f` session with the plugin sourced manually.

### AWS CLI: default to a work profile and pick an alternative

Save this as `aws.yaml` in your manifest directory and run `shell-overrides reload`.
Use the names of profiles you already have configured in the AWS CLI.

```yaml
version: 1
commands:
  aws:
    - prefix: []
      defaults:
        - append: [--profile=work-read-dev]
          names: [--profile]
          forms: [equals, separate]
tokens:
  PROFILE_DEV: work-read-dev
  PROFILE_STAGE: work-read-stage
  PROFILE_PROD: work-read-prod
```

```zsh
aws sts get-caller-identity                          # adds --profile=work-read-dev
aws s3 ls --profile=work-read-stage                  # keeps the explicit profile
aws --profile work-read-prod sts get-caller-identity # also recognizes the two-argument form
```

With token-completion loaded, type `aws s3 ls --profile=$PROFILE_` and press
**Tab**. Pick `PROFILE_STAGE` from `aws.yaml`. The editable line now
contains the quoted literal `work-read-stage` after `--profile=`. When you
run it, the wrapper sees the explicit option and leaves it alone.

The shared `PROFILE_` prefix gives you an enum-like menu of familiar values.
You can still type any configured profile. The tokens are picker entries;
they do not create shell variables or configure credentials.

### Discover AWS profiles from an INI file

As an alternative to maintaining the static profile tokens above, replace the
AWS rule with this one. Save it in your manifest directory and run
`shell-overrides reload`. Load [token-completion](https://github.com/randallwick/token-completion)
with contextual provider support; if you select providers explicitly, include
`shell_overrides`.

```yaml
version: 1
commands:
  aws:
    - prefix: []
      defaults:
        - append: [--profile=work-read-dev]
          names: [--profile]
          forms: [equals, separate]
      completions:
        - option: --profile
          forms: [equals, separate]
          source:
            format: ini
            file: ~/.aws/credentials
            select: section-names
```

With sections `[work-read-dev]` and `[work-read-stage]`, type
`aws s3 ls --profile=$wo` or `aws s3 ls --profile $wo` and press **Tab**.
The picker shows the two section names. Choose the staging profile, and the
editable line contains token-completion’s quoted file lookup expression. AWS
receives one explicit profile value, `work-read-stage`. Credential option
names and values never appear as candidates, and the generated lookup can
only return a section name. Omitting `--profile` still supplies the default.
Bare `--profile <Tab>` continues through normal zsh completion.

### Walk a project settings file one component at a time

For a CLI called `deploy`, save the following manifest and reload:

```yaml
version: 1
commands:
  deploy:
    - prefix: [show]
      completions:
        - option: --region
          source: {format: json, file: ./settings.json, select: leaf-values}
```

Given this `settings.json`:

```json
{"deployment": {"region": "us-east-1"}, "dependencies": {"registry": "local"}}
```

Type `deploy show --region=$de.re` and press **Tab**. Both first components
match. Choosing `deployment.` expands the token to `$deployment.re`.
Press Tab again to select the `region` leaf. With an unambiguous prefix such
as `$depl.reg`, one Tab reaches the leaf directly. The saved command reads
`settings.json` in its execution directory, so replay in another project
uses that project's region. Selecting a branch leaves the command editable.

### kubectl: default a namespace for reads

Save this as `kube.yaml` alongside the AWS manifest and reload. The rule
only matches commands starting with `kubectl get`. Asking for all namespaces
skips it entirely.

```yaml
version: 1
commands:
  kubectl:
    - prefix: [get]
      exclude:
        - names: [--all-namespaces, -A]
          forms: [bare, equals]
      defaults:
        - append: [--namespace=payments-dev]
          names: [--namespace, -n]
          forms: [equals, separate, attached]
tokens:
  NS_DEV: payments-dev
  NS_STAGE: payments-stage
```

```zsh
kubectl get pods                  # adds --namespace=payments-dev
kubectl get pods -n payments-stage # keeps the explicit namespace
kubectl get deployments -A        # lists across namespaces
```

Type `kubectl get pods --namespace=$NS_` and press **Tab** to choose a
namespace by name. Other subcommands keep their usual behavior.

### Exclude a rule and fall through

For an `inspect` subcommand, select a human-readable layout unless the caller
requests machine output. The next eligible rule may still supply other defaults.

```yaml
version: 1
commands:
  report:
    - prefix: [inspect]
      exclude:
        - names: [--json]
      defaults:
        - append: [--pretty]
    - prefix: [inspect]
      defaults:
        - append: [--quiet]
```

```zsh
report inspect item           # argv: inspect item --pretty
report inspect --json item    # argv: inspect --json item --quiet
```

### Offer a literal to token completion

```yaml
version: 1
tokens:
  REGION: us-east-2
  GREETING: 'hello $USER'
```

With [token-completion](https://github.com/randallwick/token-completion)
loaded, type `printf '%s\n' $GREET` and press Tab. Accepting the manifest
candidate inserts a shell-quoted literal. Running the line prints
`hello $USER`. If a shell variable also supplies that name, the picker offers
both and labels the manifest candidate with its filename. An explicit
`TOKEN_COMPLETION_PROVIDERS` array should include `shell_overrides` to enable
the provider. Either plugin may load first. The provider reads the active rule
inventory, so manifest edits reach completion on refresh as well.

## Manifest reference

The default directory is `${XDG_CONFIG_HOME:-$HOME/.config}/shell-overrides/`.
Write one or more top-level, nonhidden `.yaml` or `.yml` files. Symlinks to
regular files work. The loader accepts UTF-8 (with or without a BOM) and
UTF-16 with a byte-order mark.

| Location | Type | Semantics |
| --- | --- | --- |
| `version` | Integer, required | Set to `1`. |
| `commands` | Mapping, optional | Maps each executable name to its ordered, nonempty list of rules. Omitted means no commands. |
| `commands.<name>[].exact` | String array | Match every argument, including the count. |
| `commands.<name>[].prefix` | String array | Match the initial arguments. An empty list matches every invocation. Each rule requires exactly one of `exact` and `prefix`. |
| `commands.<name>[].exclude` | Option-group array, optional | A recognized option in any group disqualifies the rule. Defaults to an empty list. |
| `commands.<name>[].defaults` | Option-group array, optional | Each group supplies its missing arguments, in list order. Each rule needs at least one default or completion. |
| `commands.<name>[].completions` | Array, optional | File sources for this rule’s options. Repeat an option with different sources to offer all of them. |
| `defaults[].append` | String array, required | One or more literal arguments to add. Defaults are inserted immediately before the first option boundary `--`, or appended at the end. |
| `defaults[].names` | Flag-name array, optional | Spellings that suppress this group. Defaults to the first `append` argument. Set `names` explicitly if `append` starts with an option containing a value, such as `--color=auto`. |
| `exclude[].names` | Flag-name array, required | Spellings that disqualify the rule. |
| `defaults[].forms`, `exclude[].forms` | String array, optional | Recognized forms in the table below. Defaults to `[bare]`. |
| `tokens` | Mapping of identifiers to strings, optional | Shell-quoted literals offered through the optional provider. Omitted means no tokens. |

Each command name starts with an ASCII letter or digit and continues with
letters, digits, `_`, `.`, `+` or `-`. Option names start with one or two hyphens
followed by a letter or digit and may continue with letters, digits, `_` or
`-`. Token names use ASCII shell identifiers. Quote YAML scalars such as
`"false"`, `"yes"` and `"1"` when they represent arguments or token values.
Unknown fields, repeated mapping keys and embedded NULs in shell strings are
invalid.

| Form | Example | Consumes |
| --- | --- | --- |
| `bare` | `--quiet` | Exact flag. |
| `equals` | `--format=json` | One argument, including an explicitly empty value in `--format=`. |
| `separate` | `--format json`, `-f ''` | Flag and following argument. The first `--` always ends option scanning. |
| `attached` | `-fjson` | Short one-letter option immediately followed by its value. |

The wrapper checks rules in manifest order and applies only the first eligible
rule. Flag scanning stops at `--`. A declared separate value belongs to its
option even if the value begins with a hyphen. Declare the forms your command
actually accepts. zsh continues to handle redirection, pipelines, quoting and
control flow.

### File completion fields

Every entry below belongs under `commands.<name>[].completions[]`.

| Field | Type and default | Meaning |
| --- | --- | --- |
| `option` | Required flag name | The exact option spelling. Add another entry for an alias. |
| `forms` | List, defaults to `[separate, equals]` | Enable `--option $fragment`, `--option=$fragment`, or both. |
| `source.format` | Required string | `ini`, `toml`, `json` or `yaml`. |
| `source.file` | Required path string | Absolute paths and leading `~` are supported. Relative paths resolve from the working directory at completion and execution. |
| `source.select` | Required string | `section-names` (INI only) and `keys` return existing top-level names. `leaf-values` walks the document and returns the selected scalar. |

Scope is determined by the command segment containing the cursor, its arguments
before the active option, and the first eligible manifest rule. The active
option/value pair is omitted when matching `prefix`, `exact` and `exclude`.
A pipe starts a new command; `--` ends option matching. A preceding option
with a declared separate value consumes its following argument even when that
argument looks like the active option. A matching source
takes precedence over generic token providers in that position. A bare `$`
after the option lists the root choices. Multiple matching sources are labelled
with their paths. Source documents are read on demand; editing one doesn't
require a manifest reload.

Path components match case-insensitive prefixes. A fully spelled intermediate
component takes precedence. A dot separates levels, and zero-based indices
select array elements. Literal dots, spaces and other shell-sensitive key
characters are percent-encoded in the path: `deploy.us%2Eeast` identifies
the single key `us.east` inside `deploy`. Lookup uses exact, case-sensitive
keys. Supported mapping keys are nonempty strings without NUL; scalar strings
preserve whitespace, including trailing newlines. Booleans and null use JSON
spelling, and date/time scalars use ISO format.

Shell-overrides selects the source declaration for the current command, rule
and option. Token-completion reads that file, presents candidates and inserts
its deferred lookup. When the line executes, token-completion asks the loaded
shell-overrides provider whether the command, option, format, selection policy
and file still match a current declaration. Removing the declaration and
running `shell-overrides reload` revokes it, including for saved history
entries. An invalid manifest edit retains the last valid declaration.

A missing key, unavailable source or revoked declaration produces a diagnostic
and an empty argument. A present empty string succeeds quietly. See the
[token-completion file provider reference](https://github.com/randallwick/token-completion)
for parser dependencies, supported documents and standalone use.

## Shell settings

Set location and interpreter variables before sourcing. Changes to these settings
are picked up by the next `shell-overrides reload`.

| Setting | Default | Purpose |
| --- | --- | --- |
| `SHELL_OVERRIDES_CONFIG_DIR` | `${XDG_CONFIG_HOME:-$HOME/.config}/shell-overrides` | Plugin configuration root. |
| `SHELL_OVERRIDES_MANIFEST_DIR` | Effective config root | Directory to scan, useful for a disposable project or test shell. |
| `SHELL_OVERRIDES_PYTHON` | `python3` in source installs, packaged interpreter under Nix | PyYAML-capable interpreter for cache misses and edits. |
| `SHELL_OVERRIDE` | Enabled | Set to `0` or `false` to bypass defaults. Prefix a single invocation or set it for a shell session. |
| `XDG_CACHE_HOME` | `$HOME/.cache` | Holds private validated state under `shell-overrides/`, isolated by manifest directory. |
| `NO_COLOR` | Unset | Disable color in terminal help. Piped output is plain. |

## Refresh and recovery

New shells discover additions, edits and removals. Run `shell-overrides reload`
to do the same in the current shell. A warm start compares filenames and raw
source bytes, then checks the compiled record checksum. A cold start compiles
the YAML under a writer lock and publishes the cache atomically. Wrapped
invocations work entirely in zsh.

A malformed edit produces a file-specific diagnostic and preserves its last
valid definitions. A malformed new file contributes nothing. Removing a file
revokes its rules even when another file contains an error. Define a command
in one file only. If definitions compete, an existing owner has precedence;
with two new files the first in bytewise filename order wins. The rejected
file keeps its previous valid rules when available. A pre-existing alias,
function, reserved word or builtin retains ownership of its name.

## Develop from a checkout

```zsh
checkout=$PWD
scratch=$(mktemp -d)
mkdir -p "$scratch/manifests"
SHELL_OVERRIDES_MANIFEST_DIR="$scratch/manifests" XDG_CACHE_HOME="$scratch/cache" \
  PLUGIN="$checkout/shell-overrides.plugin.zsh" zsh -f
# In the disposable shell:
source "$PLUGIN"
shell-overrides --help
```

Edit a manifest and run `shell-overrides reload`. Open another disposable shell
to exercise plugin changes from a fresh startup. The regression suite runs from
the checkout using a Python interpreter that can import PyYAML:

```sh
python3 -m unittest discover -s tests -v
```
