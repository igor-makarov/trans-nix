--- Lists live indexed versions for a nixpkgs attribute.
--- @param ctx BackendListVersionsCtx
--- @return BackendListVersionsResult
function PLUGIN:BackendListVersions(ctx)
    if not ctx.tool or ctx.tool == "" then
        error("Tool name cannot be empty")
    end

    local options = ctx.options or {}
    if options.output ~= nil and (type(options.output) ~= "string" or options.output == "") then
        error("trans-nix option 'output' must be a non-empty string")
    end
    if options.package ~= nil and (type(options.package) ~= "string" or options.package == "") then
        error("trans-nix option 'package' must be a non-empty string")
    end
    local json = require("json")
    local trans_nix = require("lib.trans_nix")
    local args = {
        "list-versions",
        options.package or ctx.tool,
        "--platform",
        trans_nix.platform(),
        "--json",
    }
    if options.output then
        table.insert(args, "--output")
        table.insert(args, options.output)
    end
    local output = trans_nix.exec(args)
    return { versions = json.decode(output) }
end
