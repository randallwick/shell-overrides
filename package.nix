{ lib, stdenvNoCC, python3, coreutils, fzf }:

let
  interpreter = python3.withPackages (ps: [ ps.pyyaml ]);
in
stdenvNoCC.mkDerivation {
  pname = "shell-overrides";
  version = "0.3.0";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    destination="$out/share/shell-overrides"
    install -Dm644 cache.py "$destination/cache.py"
    install -m644 shell-overrides.plugin.zsh "$destination/"
    substituteInPlace "$destination/shell-overrides.plugin.zsh" \
      --replace-fail 'SHELL_OVERRIDES_PYTHON:-python3' 'SHELL_OVERRIDES_PYTHON:-${interpreter}/bin/python3'
    substituteInPlace "$destination/shell-overrides.plugin.zsh" \
      --replace-fail 'command fzf' 'command ${fzf}/bin/fzf' \
      --replace-fail 'command cksum' 'command ${coreutils}/bin/cksum'
    substituteInPlace "$destination/cache.py" \
      --replace-fail '["cksum"]' '["${coreutils}/bin/cksum"]'
    runHook postInstall
  '';
  meta = {
    description = "Declarative command defaults for zsh";
    homepage = "https://github.com/randallwick/shell-overrides";
    platforms = lib.platforms.unix;
  };
}
