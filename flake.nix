{
  description = "Nix packages for pdf-processing-core";

  inputs = {
    agent-env-nix.url = "github:RekiDunois/agent-env-nix/main";
    nixpkgs.follows = "agent-env-nix/nixpkgs";
  };

  outputs = { nixpkgs, agent-env-nix, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
      mkPdfPackages = { pkgs, enableHarnesses ? [ "codex" ] }:
        import ./nix/pdfx.nix {
          inherit pkgs enableHarnesses;
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

