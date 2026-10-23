{ pkgs, pythonPackages }:

let
  inherit (pkgs) darwin lib;
  inherit (pythonPackages) pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz;

  # Match the PyObjC framework packaging in the caller's pinned Nixpkgs.
  # Both missing bindings use the same source and Python ABI as pyobjc-core.
  mkFramework = framework: extraDependencies:
    pythonPackages.buildPythonPackage {
      pname = "pyobjc-framework-${framework}";
      pyproject = true;
      inherit (pyobjc-core) version src;
      sourceRoot = "${pyobjc-core.src.name}/pyobjc-framework-${framework}";

      build-system = [ pythonPackages.setuptools ];
      buildInputs = [ darwin.libffi ];
      nativeBuildInputs = [ darwin.DarwinTools ];

      # Same SDK-tool compatibility patch as Nixpkgs' Quartz/AVFoundation.
      postPatch = ''
        substituteInPlace pyobjc_setup.py \
          --replace-fail "-buildversion" "-buildVersion" \
          --replace-fail "-productversion" "-productVersion" \
          --replace-fail "/usr/bin/sw_vers" "sw_vers" \
          --replace-fail "/usr/bin/xcrun" "xcrun"
      '';

      dependencies = [ pyobjc-core pyobjc-framework-Cocoa ] ++ extraDependencies;
      env.NIX_CFLAGS_COMPILE = toString [
        "-I${darwin.libffi.dev}/include"
        "-Wno-error=unused-command-line-argument"
      ];
      pythonImportsCheck = [ framework ];

      meta = {
        description = "PyObjC wrappers for the ${framework} framework on macOS";
        homepage = "https://github.com/ronaldoussoren/pyobjc/tree/main/pyobjc-framework-${framework}";
        license = lib.licenses.mit;
        platforms = lib.platforms.darwin;
      };
    };

  coreML = mkFramework "CoreML" [ ];
  vision = mkFramework "Vision" [ pyobjc-framework-Quartz coreML ];
in
{
  inherit coreML vision;
}
