-- Track tiling positions of hidden apps so we can restore them on re-show.
local hiddenAppPositions = {}

-- Returns window IDs sorted by x position (left to right) using Hammerspoon frames.
-- AeroSpace list-windows order is arbitrary, so we can't use list index as visual position.
local function sortedByX(winIds)
    local result = {}
    for _, winId in ipairs(winIds) do
        local w = hs.window.get(tonumber(winId))
        if w then
            table.insert(result, { id = winId, x = w:frame().x })
        end
    end
    table.sort(result, function(a, b) return a.x < b.x end)
    return result
end

function openOrHideApp(appConfig)
    -- bundleID is more reliable, so use it if provided
    local appID = appConfig.bundleID or appConfig.name
    local app = hs.application.get(appID)

    -- App is open. Let's decide what to do with it.
    if app and app:isRunning() then
        local window = app:mainWindow()
        -- If window exists and it is frontmost, hide it.
        if window and app:isFrontmost() then
            -- Pre-focus another window in the current workspace before hiding.
            -- This ensures macOS hands focus to a window on this workspace when
            -- the app hides, preventing AeroSpace from following focus elsewhere.
            local ws = hs.execute("/opt/homebrew/bin/aerospace list-workspaces --monitor focused --visible"):gsub("%s+", "")
            local focusedWin = tostring(window:id())
            local wsWins = hs.execute(string.format("/opt/homebrew/bin/aerospace list-windows --workspace %s --format '%%{window-id}'", ws))

            local winIds = {}
            for winId in wsWins:gmatch("%d+") do
                table.insert(winIds, winId)
            end

            -- Sort by x to find the window's true visual (left-to-right) position.
            local sorted = sortedByX(winIds)
            local focusedIdx = nil
            for i, entry in ipairs(sorted) do
                if entry.id == focusedWin then focusedIdx = i; break end
            end

            local prefocused = false
            for _, winId in ipairs(winIds) do
                if winId ~= focusedWin then
                    local w = hs.window.get(tonumber(winId))
                    if w then w:focus(); prefocused = true; break end
                end
            end

            -- Record position so we can restore it when the app is re-shown.
            if focusedIdx then
                hiddenAppPositions[appID] = {
                    focusedIdx = focusedIdx,
                    totalWindows = #sorted
                }
            end

            app:hide()
            -- If no other window existed to pre-focus, fall back to workspace re-assertion
            if not prefocused and ws ~= "" then
                hs.timer.doAfter(0.05, function()
                    hs.execute("/opt/homebrew/bin/aerospace workspace " .. ws)
                end)
            end
        -- App is not frontmost, so try to show it.
        else
            if window then
                -- Show the app's window if one already exists.
                app:mainWindow():focus()
                -- Restore tiling position if the app was hidden by us.
                -- We sort windows by x position to find the actual visual position,
                -- then move left until we reach the stored target index.
                local posInfo = hiddenAppPositions[appID]
                if posInfo then
                    hiddenAppPositions[appID] = nil
                    local targetWinId = tostring(window:id())
                    hs.timer.doAfter(0.15, function()
                        -- Re-assert focus on our window so AeroSpace moves the right one.
                        hs.execute("/opt/homebrew/bin/aerospace focus --window-id " .. targetWinId)
                        local ws = hs.execute("/opt/homebrew/bin/aerospace list-workspaces --monitor focused --visible"):gsub("%s+", "")
                        local wsWins = hs.execute(string.format("/opt/homebrew/bin/aerospace list-windows --workspace %s --format '%%{window-id}'", ws))
                        local winIds = {}
                        for winId in wsWins:gmatch("%d+") do
                            table.insert(winIds, winId)
                        end
                        local sorted = sortedByX(winIds)
                        local currentIdx = nil
                        for i, entry in ipairs(sorted) do
                            if entry.id == targetWinId then currentIdx = i; break end
                        end
                        if currentIdx then
                            local movesNeeded = currentIdx - posInfo.focusedIdx
                            for _ = 1, movesNeeded do
                                hs.execute("/opt/homebrew/bin/aerospace move left")
                            end
                        end
                    end)
                end
            elseif appConfig.newWindowMenuItem then
                -- Otherwise try to create a new window.
                -- hs.application.frontmostApplication():getMenuItems(function(result) print(string.format("result: %s", hs.inspect(result))) end)
                app:selectMenuItem(appConfig.newWindowMenuItem)
                app:activate()
            end
        end
    -- App is not open. Launch it.
    else
        hs.application.open(appID)
        -- hs.application.launchOrFocus(appID)
    end
end
