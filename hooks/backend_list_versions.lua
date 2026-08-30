--- Lists live nixmultiverse versions for a nixpkgs attribute.
--- This hook uses mise's HTTP module because Python dependencies are not yet
--- available while mise is resolving the install graph. The standalone CLI
--- exposes the same operation for direct use and testing.
--- @param ctx BackendListVersionsCtx
--- @return BackendListVersionsResult
function PLUGIN:BackendListVersions(ctx)
    if not ctx.tool or ctx.tool == "" then
        error("Tool name cannot be empty")
    end

    local http = require("http")
    local json = require("json")
    local semver = require("semver")
    local trans_nix = require("lib.trans_nix")
    local platform = trans_nix.platform()
    local directory = platform == "x86_64-linux" and "meta" or "meta-" .. platform
    local shard = ctx.tool:sub(1, 2):lower():gsub("[^%w]", "_")
    if shard == "" then
        shard = "_"
    end
    local url = "https://nixmultiverse.com/" .. directory .. "/" .. shard .. ".json"
    local response = http.get({
        url = url,
        headers = { ["User-Agent"] = "trans-nix/1" },
    })
    if response.status_code ~= 200 then
        error("nixmultiverse returned HTTP " .. response.status_code .. " for " .. ctx.tool)
    end

    local metadata = json.decode(response.body)
    local entries = metadata.attrs and metadata.attrs[ctx.tool]
    if type(entries) ~= "table" then
        error("nixpkgs attribute not found in nixmultiverse: " .. ctx.tool)
    end

    local versions = {}
    for version, entry in pairs(entries) do
        local digest = type(entry) == "table" and entry.d or nil
        if
            type(version) == "string"
            and type(digest) == "string"
            and #digest == 32
            and digest:match("^[0123456789abcdfghijklmnpqrsvwxyz]+$")
            and entry.ok ~= 0
        then
            table.insert(versions, version)
        end
    end
    versions = semver.sort(versions)
    if #versions == 0 then
        error("trans-nix found no live indexed versions for " .. ctx.tool)
    end
    return { versions = versions }
end
