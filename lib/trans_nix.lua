local M = {}

local function shell_quote(value)
    return "'" .. tostring(value):gsub("'", "'\"'\"'") .. "'"
end

function M.platform()
    local os_type = RUNTIME.osType:lower()
    local arch_type = RUNTIME.archType:lower()

    if os_type == "linux" then
        if arch_type == "amd64" or arch_type == "x86_64" or arch_type == "x64" then
            return "x86_64-linux"
        end
        if arch_type == "arm64" or arch_type == "aarch64" then
            return "aarch64-linux"
        end
    elseif os_type == "darwin" then
        if arch_type == "arm64" or arch_type == "aarch64" then
            return "aarch64-darwin"
        end
    end

    error("trans-nix does not support this platform: " .. os_type .. "/" .. arch_type)
end

function M.exec(args)
    local cmd = require("cmd")
    local file = require("file")
    local cli = file.join_path(RUNTIME.pluginDirPath, "bin", "trans-nix")
    local command = { shell_quote(cli) }
    for _, arg in ipairs(args) do
        table.insert(command, shell_quote(arg))
    end
    return cmd.exec(table.concat(command, " "))
end

return M
