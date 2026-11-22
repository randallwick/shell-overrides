typeset -g _so_root=${${(%):-%x}:A:h}
typeset -ga _so_data _so_token_files _so_token_keys _so_token_values _so_wrapped
typeset -gA _so_start

_so_prepare_argv() {
  emulate -L zsh
  local target=$1
  shift
  # A command block contains ordered rules, each followed by its flag groups.
  local -i pos=${_so_start[$target]} count=_so_data[pos++] r n e d i j k limit matched
  local -i spec appends names forms
  local mode flag form arg groups
  local -a additions starts seen
  local -A bare separate equals attached
  for ((r=0; r<count; r++)); do
    mode=${_so_data[pos++]}
    n=_so_data[pos++]
    matched=1
    if (( $# < n )) || [[ $mode == exact && $# -ne n ]]; then
      matched=0
    fi
    for ((i=1; i<=n; i++)); do
      [[ ${argv[i]} == "${_so_data[pos++]}" ]] || matched=0
    done
    e=_so_data[pos++] d=_so_data[pos++]
    bare=() separate=() equals=() attached=() starts=() seen=()
    for ((spec=1; spec<=e+d; spec++)); do
      starts+=($pos)
      appends=_so_data[pos++]
      (( pos += appends ))
      names=_so_data[pos++]
      k=pos
      (( pos += names ))
      forms=_so_data[pos++]
      for ((i=0; i<forms; i++)); do
        form=${_so_data[pos++]}
        for ((j=k; j<k+names; j++)); do
          flag=${_so_data[j]}
          case $form in
            bare) bare[$flag]+=" $spec" ;;
            separate) separate[$flag]+=" $spec" ;;
            equals) equals[$flag]+=" $spec" ;;
            attached) [[ $flag == -? ]] && attached[$flag]+=" $spec" ;;
          esac
        done
      done
    done
    (( matched )) || continue
    limit=$#
    for ((i=1; i<=$#; i++)); do
      arg=${argv[i]}
      if [[ $arg == -- ]]; then
        limit=i-1
        break
      fi
      [[ $arg == -* ]] || continue
      groups=${bare[$arg]}
      if [[ -n ${separate[$arg]} && i -lt $# && ${argv[i+1]} != -- ]]; then
        groups+=${separate[$arg]}
        (( i++ ))
      else
        if [[ $arg == *=* ]]; then
          flag=${arg%%=*}
          groups+=${equals[$flag]}
        fi
        if [[ $arg == -?*? ]]; then
          flag=${arg[1,2]}
          groups+=${attached[$flag]}
        fi
      fi
      for spec in ${=groups}; do
        seen[spec]=1
      done
    done
    for ((i=1; i<=e; i++)); do
      [[ -n ${seen[i]} ]] && matched=0
    done
    (( matched )) || continue
    for ((i=e+1; i<=e+d; i++)); do
      [[ -n ${seen[i]} ]] && continue
      k=starts[i]
      appends=_so_data[k++]
      for ((j=0; j<appends; j++)); do
        additions+=("${_so_data[k++]}")
      done
    done
    _so_argv=("${(@)argv[1,limit]}" "${additions[@]}" "${(@)argv[limit+1,-1]}")
    return 0
  done
  return 0
}

_so_warm_records() {
  emulate -L zsh
  local LC_ALL=C file raw prefix snapshot payload byte_count
  local checksum expected
  local nul=$'\0'
  zmodload zsh/mapfile || return 1
  [[ -r $cache/warm ]] || return 1

  # Under the C locale, lengths and slices count bytes. A length-framed
  # manifest can include BOMs, NULs, and trailing newlines.
  prefix="SO/2${nul}${config}${nul}"
  for file in "$config"/*.y(a|)ml(N-.); do
    [[ -r $file ]] || return 1
    raw=${mapfile[$file]}
    prefix+="${file:t}${nul}${#raw}${nul}${raw}"
  done
  prefix+=$nul
  snapshot=${mapfile[$cache/warm]}
  [[ $snapshot == "${prefix}"* ]] || return 1
  payload=${snapshot#"$prefix"}
  expected=${payload%%"$nul"*}
  payload=${payload#*"$nul"}
  byte_count=${payload%%"$nul"*}
  [[ $byte_count == <-> ]] || return 1
  payload=${payload#*"$nul"}
  [[ $payload == *"done${nul}" ]] || return 1
  payload=${payload%"done${nul}"}
  [[ $byte_count == "${#payload}" ]] || return 1
  checksum=$(print -rn -- "$payload" | command cksum) || return 1
  [[ ${checksum%%[[:space:]]*} == "$expected" ]] || return 1
  records=("${(@0)payload}done")
}

_so_reload() {
  emulate -L zsh
  local config_root=${SHELL_OVERRIDES_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/shell-overrides}
  local config=${SHELL_OVERRIDES_MANIFEST_DIR:-$config_root}
  local cache_root=${XDG_CACHE_HOME:-$HOME/.cache}/shell-overrides
  config=${config:a}
  # Alternate directory markers with path components. Independent manifest
  # roots keep their own last-valid rules, even in the same shell session.
  local cache=$cache_root/dirs${config//\//\/d\/}
  local -a records
  local record name
  local -i pos=1 n end
  if ! _so_warm_records; then
    while IFS= read -r -d '' record; do
      records+=("$record")
    done < <(command "${SHELL_OVERRIDES_PYTHON:-python3}" "$_so_root/cache.py" "$config" "$cache" "$cache_root" && printf 'done\0')
  fi
  [[ ${records[-1]} == done ]] || return 1
  for name in "${_so_wrapped[@]}"; do
    [[ ${functions[$name]} == ${functions[_so_wrapper]} ]] && unfunction "$name"
  done
  _so_data=() _so_wrapped=() _so_start=()
  _so_token_files=() _so_token_keys=() _so_token_values=()
  while ((pos < $#records)); do
    case ${records[pos++]} in
      error) print -ru2 -- "${records[pos++]}" ;;
      command)
        name=${records[pos++]}
        n=records[pos++]
        end=pos+n-1
        if (( ${+functions[$name]} || ${+builtins[$name]} || ${+aliases[$name]} || ${reswords[(Ie)$name]} )); then
          print -u2 -r -- "shell-overrides: cannot wrap shell command $name"
        else
          _so_start[$name]=$(($#_so_data+1))
          _so_data+=("${(@)records[pos,end]}")
          functions[$name]=${functions[_so_wrapper]}
          _so_wrapped+=("$name")
        fi
        pos=end+1
        ;;
      token)
        _so_token_files+=("${records[pos++]}")
        _so_token_keys+=("${records[pos++]}")
        _so_token_values+=("${records[pos++]}")
        ;;
    esac
  done
}
_so_wrapper() {
  emulate -L zsh
  if [[ ${SHELL_OVERRIDE:-} == 0 || ${SHELL_OVERRIDE:-} == false ]]; then
    command "${funcstack[1]}" "$@"
    return $?
  fi
  local -a _so_argv=("$@")
  _so_prepare_argv "${funcstack[1]}" "$@"
  # Run outside the preparation scope so its local names cannot mask the
  # caller's exported environment when the executable starts.
  command "${funcstack[1]}" "${_so_argv[@]}"
}

typeset -g token_provider_shell_overrides_version=1
token_provider_shell_overrides_query() {
  emulate -L zsh
  local -i i
  for ((i=1; i<=$#_so_token_keys; i++)); do
    token_completion_emit "${_so_token_keys[i]}" "${_so_token_files[i]}" "${(qqqq)_so_token_values[i]}"
  done
}

shell-overrides() {
  emulate -L zsh
  case "$1:$#" in
    reload:1) _so_reload ;;
    --help:1|-h:1|help:1|:0)
      local title='shell-overrides'
      if [[ -t 1 && $TERM != dumb && -z ${NO_COLOR+x} ]]; then
        title=$'\e[1;36mshell-overrides\e[0m'
      fi
      print -r -- "$title  ·  declarative defaults for zsh commands"
      print
      print -r -- '  reload      Refresh manifests in this shell'
      print -r -- '  --help      Show this help'
      ;;
    *) print -u2 -r -- 'usage: shell-overrides reload | --help'; return 2 ;;
  esac
}
_so_reload
