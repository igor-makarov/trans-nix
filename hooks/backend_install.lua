--- Installs and links one resolved nixpkgs package closure.
--- @param ctx BackendInstallCtx
--- @return BackendInstallResult
function PLUGIN:BackendInstall(ctx)
    if not ctx.tool or ctx.tool == "" then
        error("Tool name cannot be empty")
    end
    if not ctx.version or ctx.version == "" then
        error("Version cannot be empty")
    end
    if not ctx.install_path or ctx.install_path == "" then
        error("Install path cannot be empty")
    end

    local options = ctx.options or {}
    local nix_package = options["nix-package"]
    local nix_package_output = options["nix-package-output"]
    local short_storage_slug = options["short-storage-slug"]
    if nix_package ~= nil and (type(nix_package) ~= "string" or nix_package == "") then
        error("trans-nix option 'nix-package' must be a non-empty string")
    end
    if nix_package_output ~= nil and (type(nix_package_output) ~= "string" or nix_package_output == "") then
        error("trans-nix option 'nix-package-output' must be a non-empty string")
    end
    if short_storage_slug ~= nil and (type(short_storage_slug) ~= "string" or short_storage_slug == "") then
        error("trans-nix option 'short-storage-slug' must be a non-empty string")
    end
    local trans_nix = require("lib.trans_nix")
    local args = {
        "install",
        nix_package or ctx.tool,
        ctx.version,
        trans_nix.platform(),
        ctx.install_path,
        "--short-storage-slug",
        short_storage_slug or ctx.tool,
        "--force",
    }
    if nix_package_output then
        table.insert(args, "--nix-package-output")
        table.insert(args, nix_package_output)
    end
    trans_nix.exec(args)
    return {}
end
