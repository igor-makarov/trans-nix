PLUGIN = { -- luacheck: ignore
    name = "trans-nix",
    version = "1.0.1",
    description = "Install relocatable nixpkgs closures through mise without Nix",
    author = "Igor Makarov",
    homepage = "https://github.com/igor-makarov/trans-nix",
    license = "MIT",
    minRuntimeVersion = "0.3.0",
    depends = { "python" },
    notes = {
        "Uses NixHub metadata and cache.nixos.org; Nix is not required.",
        "Configure Python 3.14+ as a mise tool before using this backend.",
        "Relocated roots persist under $HOME/.tn after mise uninstall.",
    },
}
