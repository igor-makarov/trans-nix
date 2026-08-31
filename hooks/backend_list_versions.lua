--- Lists live indexed versions for a nixpkgs attribute.
--- @param ctx BackendListVersionsCtx
--- @return BackendListVersionsResult
function PLUGIN:BackendListVersions(ctx)
    if not ctx.tool or ctx.tool == "" then
        error("Tool name cannot be empty")
    end

    local options = ctx.options or {}
    local nix_package = options["nix-package"]
    local nix_package_output = options["nix-package-output"]
    if nix_package ~= nil and (type(nix_package) ~= "string" or nix_package == "") then
        error("trans-nix option 'nix-package' must be a non-empty string")
    end
    if nix_package_output ~= nil and (type(nix_package_output) ~= "string" or nix_package_output == "") then
        error("trans-nix option 'nix-package-output' must be a non-empty string")
    end
    local json = require("json")
    local trans_nix = require("lib.trans_nix")
    local args = {
        "list-versions",
        nix_package or ctx.tool,
        trans_nix.platform(),
        "--json",
    }
    if nix_package_output then
        table.insert(args, "--nix-package-output")
        table.insert(args, nix_package_output)
    end
    local output = trans_nix.exec(args)
    return { versions = json.decode(output) }
end
