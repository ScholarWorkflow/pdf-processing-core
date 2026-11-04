{
  description = "Nix packages for pdf-processing-core";

  inputs = {
    agent-env-nix.url = "github:RekiDunois/agent-env-nix/main";
    base-skills.url = "github:ScholarWorkflow/base-skills/main";
    base-skills.inputs.agent-env-nix.follows = "agent-env-nix";
    base-skills.inputs.nixpkgs.follows = "nixpkgs";
    nixpkgs.follows = "agent-env-nix/nixpkgs";
  };

  outputs = { nixpkgs, agent-env-nix, base-skills, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
      mkPdfPackages = { pkgs, enableHarnesses ? [ "codex" ] }:
        import ./nix/pdfx.nix {
          inherit pkgs enableHarnesses;
          # base-skills exposes the runtime for Darwin only, so Linux keeps the
          # pre-existing package without it instead of failing evaluation.
          visionRuntime =
            if pkgs.stdenv.hostPlatform.isDarwin
            then base-skills.lib.mkVisionRuntime { inherit pkgs; python = pkgs.python3; }
            else null;
          mkAgentPackage = agent-env-nix.lib.mkAgentPackage;
        };
      productsFor = system: mkPdfPackages {
        pkgs = nixpkgs.legacyPackages.${system};
      };
    in
    {
      lib = { inherit mkPdfPackages; };

      packages = eachSystem (system:
        let
          products = productsFor system;
        in
        {
          pdfx = products.pdfx;
          pdfx-agent = products.pdfxAgent;
          default = products.pdfx;
        });

      devShells = eachSystem (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          products = productsFor system;
        in
        {
          default = pkgs.mkShell {
            packages = [ products.python products.pdfx ];
          };
        });
    };
}

