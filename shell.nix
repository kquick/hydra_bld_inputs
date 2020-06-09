{ pkgs ? import <nixpkgs> {}
}:
let
  callPackage = pkgs.newScope (pkgs // pkgs.python37Packages);

  proj = callPackage ./default.nix {};

  # prolog = builtins.head (builtins.filter (d: d.pname == "swi-prolog") briareus.buildInputs);

in
pkgs.lib.overrideDerivation proj (drv: {
  src = ./.;
  shellHook = ''
    # Facilitate running pytest or local hh runs.
    export PATH=${pkgs.mypy}/bin:$PATH
    export PYTHONPATH=$(pwd):$PYTHONPATH
  '';
})
