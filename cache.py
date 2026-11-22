"""Compile manifests into data records for zsh. Called only on a cache miss."""
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

FORMAT = 2
NAME = r"[A-Za-z_][A-Za-z_0-9]*"
COMMAND = r"[A-Za-z0-9][A-Za-z0-9_.+-]*"
FLAG = r"--?[A-Za-z0-9][A-Za-z0-9_-]*"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(value, allowed):
    require(isinstance(value, dict) and value.keys() <= set(allowed),
            f"expected mapping with fields from {allowed}")


def string(value):
    require(isinstance(value, str) and "\0" not in value, "expected NUL-free string")
    value.encode("utf-8")
    return value


def strings(value):
    require(isinstance(value, list), "expected argument list")
    return [string(arg) for arg in value]


def mapping(value):
    require(isinstance(value, dict), "expected mapping")
    return value.items()


def option(spec, default):
    fields(spec, ["append", "names", "forms"] if default else ["names", "forms"])
    append = strings(spec["append"]) if default else []
    names = strings(spec.get("names", append[:1]))
    forms = strings(spec.get("forms", ["bare"]))
    require(names and all(re.fullmatch(FLAG, n) for n in names), "expected option names")
    require(forms and set(forms) <= {"bare", "separate", "equals", "attached"},
            "unknown option form")
    require(not default or append, "expected nonempty append")
    return [len(append), *append, len(names), *names, len(forms), *forms]


def completion(spec):
    fields(spec, ["option", "position", "forms", "markerless", "source"])
    require(("option" in spec) != ("position" in spec), "choose one of option or position")
    if "option" in spec:
        selector = string(spec["option"])
        require(re.fullmatch(FLAG, selector), "invalid completion option")
        forms = strings(spec.get("forms", ["separate", "equals"]))
        require(forms and set(forms) <= {"separate", "equals"}, "unknown completion form")
    else:
        position = spec["position"]
        require(type(position) is int and position >= 1, "position must be a positive integer")
        require("forms" not in spec, "forms applies only to an option")
        selector, forms = f"@{position}", ["positional"]
    empty = "off"
    if "markerless" in spec:
        settings = spec["markerless"]
        fields(settings, ["empty"])
        empty = string(settings["empty"])
        require(empty in {"all", "native"}, "markerless.empty must be all or native")
    src = spec["source"]
    fields(src, ["format", "file", "select"])
    fmt = string(src.get("format", "auto"))
    file, select = string(src["file"]), string(src["select"])
    require(fmt in {"auto", "ini", "toml", "json", "yaml"} and file, "expected a format and file")
    require(select in {"keys", "leaf-values"} or (fmt in {"auto", "ini"} and select == "section-names"),
            "expected keys, leaf-values, or INI section-names")
    return [selector, " ".join(forms), fmt, select, file, empty]


def compile_yaml(raw):
    try:
        import yaml
    except ImportError:
        sys.exit("shell-overrides: PyYAML is missing; install it for this interpreter or set SHELL_OVERRIDES_PYTHON")

    class UniqueLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            result = super().construct_mapping(node, deep)
            require(len(result) == len(node.value), "duplicate mapping key")
            return result

    try:
        doc = yaml.load(raw, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError(str(exc)) from exc
    fields(doc, ["version", "commands", "tokens"])
    require(type(doc.get("version")) is int and doc["version"] == 1, "expected version 1")
    commands = {}
    sources = []
    for name, rules in mapping(doc.get("commands", {})):
        require(re.fullmatch(COMMAND, string(name)), "invalid command name")
        require(isinstance(rules, list) and rules, "expected nonempty rule list")
        data = [len(rules)]
        for index, rule in enumerate(rules):
            fields(rule, ["prefix", "exact", "exclude", "defaults", "completions"])
            require(("exact" in rule) != ("prefix" in rule), "choose exact or prefix")
            mode = "exact" if "exact" in rule else "prefix"
            args = strings(rule[mode])
            excludes, defaults = rule.get("exclude", []), rule.get("defaults", [])
            completions = rule.get("completions", [])
            require(all(isinstance(group, list) for group in (defaults, excludes, completions))
                    and (defaults or completions), "expected defaults or completions")
            sources.extend([name, index, *completion(spec)] for spec in completions)
            data.extend([mode, len(args), *args, len(excludes), len(defaults)])
            for spec in excludes:
                data.extend(option(spec, False))
            for spec in defaults:
                data.extend(option(spec, True))
        commands[name] = data
    tokens = {}
    for key, value in mapping(doc.get("tokens", {})):
        require(re.fullmatch(NAME, string(key)), "invalid token name")
        tokens[key] = string(value)
    return dict(commands=commands, tokens=tokens, sources=sources)


def publish(target, content):
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
        try:
            tmp.write(content)
            tmp.flush()
            os.replace(tmp.name, target)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def records_for(files):
    records = []
    for filename, entry in files.items():
        for name, data in entry["commands"].items():
            records.extend(["command", name, len(data), *data])
        for source in entry["sources"]:
            records.extend(["source", *source])
        for key, value in entry["tokens"].items():
            records.extend(["token", filename, key, value])
    return records


def pack(records):
    return b"".join(os.fsencode(str(record)) + b"\0" for record in records)


def load_state(file, config, version):
    try:
        state = json.loads(file.read_bytes())
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict) or state.get("config") != str(config) or state.get("format") != version:
        return {}
    files = state.get("files")
    return files if isinstance(files, dict) else {}


def last_valid(entries, name, legacy):
    data = entries.get(name)
    if data is None:
        return None
    try:
        if legacy:
            raw = data["source"].encode()
        else:
            raw = base64.b64decode(data, validate=True)
        return raw, compile_yaml(raw)
    except (AttributeError, ValueError, KeyError, TypeError, RecursionError):
        return None


def remove_conflicts(files, previous, config, diagnostics):
    # Ownership can move when a surviving file drops its claim. Rolling a
    # different file back may reinstate another claim; settle all of them.
    while True:
        claims = {}
        for name, (_, compiled) in files.items():
            for command in compiled["commands"]:
                claims.setdefault(command, []).append(name)
        rejected = {}
        for command, claimants in claims.items():
            if len(claimants) > 1:
                incumbent = next((n for n in claimants if n in previous and
                                  command in previous[n][1]["commands"]), claimants[0])
                for name in claimants:
                    if name != incumbent:
                        rejected[name] = f"{command} already defined in {incumbent}"
        if not rejected:
            return
        for name, reason in rejected.items():
            diagnostics.extend(["error", f"shell-overrides: {config / name}: {reason}"])
            if name in previous and files[name] != previous[name]:
                files[name] = previous[name]
            else:
                del files[name]


def snapshot(config, sources, records):
    # Byte lengths let UTF-16 sources contain arbitrary NULs. Rule records
    # have a separate, NUL-free string contract suitable for zsh argv.
    parts = [b"SO/5\0", os.fsencode(config), b"\0"]
    for name, raw in sources:
        parts.extend([os.fsencode(name), b"\0", str(len(raw)).encode(), b"\0", raw])
    # POSIX cksum is available in source installs on macOS and Linux.
    checksum = subprocess.run(["cksum"], input=records, capture_output=True, check=True).stdout.split()[0]
    parts.extend([b"\0", checksum, b"\0", str(len(records)).encode(), b"\0", records, b"done\0"])
    return b"".join(parts)


def refresh(config, cache, cache_root):
    os.umask(0o077)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (cache / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_file = cache / "state.json"
        legacy = not state_file.exists()
        if legacy:
            entries = load_state(cache_root / "state.json", config, 1)
        else:
            entries = load_state(state_file, config, FORMAT)
        previous, files, sources, diagnostics = {}, {}, [], []
        paths = sorted((p for p in config.glob("*") if not p.name.startswith(".")
                        and p.suffix in {".yaml", ".yml"} and p.is_file()), key=os.fsencode)
        for path in paths:
            valid = last_valid(entries, path.name, legacy)
            if valid:
                previous[path.name] = valid
            try:
                raw = path.read_bytes()
                sources.append((path.name, raw))
                if not valid or raw != valid[0]:
                    valid = raw, compile_yaml(raw)
            except (OSError, ValueError, KeyError, RecursionError) as exc:
                diagnostics.extend(["error", f"shell-overrides: {path}: {exc}"])
            if valid:
                files[path.name] = valid
        remove_conflicts(files, previous, config, diagnostics)
        state = dict(format=FORMAT, config=str(config),
                     files={name: base64.b64encode(raw).decode() for name, (raw, _) in files.items()})
        publish(state_file, json.dumps(state).encode())
        records = pack(diagnostics + records_for({name: entry for name, (_, entry) in files.items()}))
        warm = cache / "warm"
        if len(sources) == len(paths):
            publish(warm, snapshot(config, sources, records))
        else:
            warm.unlink(missing_ok=True)
    sys.stdout.buffer.write(records)


if __name__ == "__main__":
    refresh(*map(Path, sys.argv[1:]))
