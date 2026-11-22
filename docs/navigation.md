# Directory navigation

Give a directory tree its own navigation command. For example, this manifest
defines `work` for the projects under `~/work`:

```yaml
version: 1
commands:
  work:
    navigate:
      root: ~/work
      env: WORK_ROOT
      markerless: {empty: all}
```

Type `work client-api` to change directory in the current shell. An exact
child name wins. If you type `work capi`, the command uses fzf’s first
case-insensitive fuzzy match. Multiple words form one query. Running `work`
with no query chooses the first match. A missing match leaves the current
directory unchanged and returns an error.

Install fzf for fuzzy execution from a source checkout. The Nix package supplies it.

With token-completion 0.3.0 or later, `work <Tab>` lists the immediate
visible child directories and symlinks to directories. The picker reads
the root each time, including after you create or rename a directory.
Type to narrow the choices, Tab to cycle and Enter to insert the selected
name. Press Enter at the shell prompt to change directory.

`root` is a literal path. A leading `~` or `~/` expands to your home, and
relative paths resolve from the current directory. Optional `env` names a
shell parameter whose nonempty value overrides the root. The plugins reserve
parameter names beginning with `_so_` and `_tc_`. Optional
`markerless.empty` selects `all` or `native` for an empty first argument.
Omit `markerless` to leave completion to zsh. A directly typed hidden child
can be selected by its exact name.
