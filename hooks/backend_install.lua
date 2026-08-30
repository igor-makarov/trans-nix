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
    local jobs = options.jobs or 16
    if type(jobs) ~= "number" or jobs < 1 or jobs % 1 ~= 0 then
        error("trans-nix option 'jobs' must be a positive integer")
    end
    if options.force ~= nil and type(options.force) ~= "boolean" then
        error("trans-nix option 'force' must be a boolean")
    end
    if options.output ~= nil and (type(options.output) ~= "string" or options.output == "") then
        error("trans-nix option 'output' must be a non-empty string")
    end
    if options.package ~= nil and (type(options.package) ~= "string" or options.package == "") then
        error("trans-nix option 'package' must be a non-empty string")
    end
    if options.install_prefix ~= nil and (type(options.install_prefix) ~= "string" or options.install_prefix == "") then
        error("trans-nix option 'install_prefix' must be a non-empty string")
    end
    local trans_nix = require("lib.trans_nix")
    local args = {
        "install",
        options.package or ctx.tool,
        ctx.version,
        ctx.install_path,
        "--platform",
        trans_nix.platform(),
        "--install-prefix",
        options.install_prefix or ctx.tool,
        "--jobs",
        tostring(jobs),
    }
    if options.output then
        table.insert(args, "--output")
        table.insert(args, options.output)
    end
    if options.force then
        table.insert(args, "--force")
    end
    trans_nix.exec(args)
    return {}
end
