{
  description = "Nix packages for pdf-processing-core";

  inputs = {
    agent-env-nix.url = "github:RekiDunois/agent-env-nix/760c1bf60e8ec19d1985660ec5e3276a8ef80b5a";
    nixpkgs.follows = "agent-env-nix/nixpkgs";
  };

  outputs = { nixpkgs, agent-env-nix, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
      productsFor = system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        import ./nix/pdfx.nix {
          inherit pkgs;
          mkAgentPackage = agent-env-nix.lib.mkAgentPackage;
        };
    in
    {
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
