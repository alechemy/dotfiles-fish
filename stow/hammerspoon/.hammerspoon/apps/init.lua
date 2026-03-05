-- Track windows parked to the hidden workspace so they persist across WS switches.
-- hiddenWindows[appID] = { windowId=string, workspace=string, positionIdx=number|nil }
local hiddenWindows = {}
local HIDDEN_WS = "H"

local aero = "/opt/homebrew/bin/aerospace"

local function getVisibleWorkspace()
    return hs.execute(aero .. " list-workspaces --monitor focused --visible"):gsub("%s+", "")
end

local function getWorkspaceWindowIds(ws)
    local raw = hs.execute(string.format("%s list-windows --workspace %s --format '%%{window-id}'", aero, ws))
    local ids = {}
    for winId in raw:gmatch("%d+") do
        table.insert(ids, winId)
    end
    return ids
end

-- Returns window IDs sorted by x position (left to right) using Hammerspoon frames.
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

-- Find the index of winId in a sorted-by-X list of workspace windows.
local function findPositionIdx(wsWinIds, winId)
    local sorted = sortedByX(wsWinIds)
    for i, entry in ipairs(sorted) do
        if entry.id == winId then return i end
    end
    return nil
end

function openOrHideApp(appConfig)
    local appID = appConfig.bundleID or appConfig.name
    local app = hs.application.get(appID)

    if app and app:isRunning() then
        local window = app:mainWindow()
        local ws = getVisibleWorkspace()

        -- Check for a parked (hidden-by-us) window, clean up if the window is gone.
        local hidden = hiddenWindows[appID]
        if hidden then
            local w = hs.window.get(tonumber(hidden.windowId))
            if not w then
                hiddenWindows[appID] = nil
                hidden = nil
            end
        end

        -- Find if the app has a window on the current workspace.
        local wsWinIds = window and getWorkspaceWindowIds(ws) or {}
        local wsWinSet = {}
        for _, id in ipairs(wsWinIds) do wsWinSet[id] = true end

        local targetWindow = nil
        if window then
            for _, w in ipairs(app:allWindows()) do
                if wsWinSet[tostring(w:id())] then
                    targetWindow = w
                    break
                end
            end
        end

        -- CASE 1: App is frontmost with a window on this workspace — park it.
        -- If frontmost but no window here (e.g. last window was closed), fall
        -- through to CASE 3 which will create a new one.
        if app:isFrontmost() and targetWindow then
            local winId = tostring(targetWindow:id())

            -- Pre-focus another window so macOS doesn't drift focus elsewhere.
            local prefocused = false
            for _, id in ipairs(wsWinIds) do
                if id ~= winId then
                    local w = hs.window.get(tonumber(id))
                    if w and w:isVisible() then w:focus(); prefocused = true; break end
                end
            end

            hiddenWindows[appID] = {
                windowId = winId,
                workspace = ws,
                positionIdx = findPositionIdx(wsWinIds, winId),
            }

            hs.execute(string.format("%s move-node-to-workspace --window-id %s %s", aero, winId, HIDDEN_WS))

            -- TUNABLE: Delay before re-asserting the workspace when the parked
            -- window was the only one here. Too short and AeroSpace may not have
            -- finished removing the node; too long and you'll notice a flicker.
            if not prefocused and ws ~= "" then
                hs.timer.doAfter(0.05, function()
                    hs.execute(aero .. " workspace " .. ws)
                end)
            end

        -- CASE 2: App not frontmost but we have a parked window — unpark it.
        elseif hidden then
            hiddenWindows[appID] = nil
            local targetWs = appConfig.summonHere and ws or hidden.workspace

            hs.execute(string.format("%s move-node-to-workspace --window-id %s %s", aero, hidden.windowId, targetWs))
            hs.execute(string.format("%s focus --window-id %s", aero, hidden.windowId))

            -- Restore tiling position.
            -- TUNABLE: Delay before repositioning. The window needs time to land
            -- in the workspace and get a frame from AeroSpace before we can read
            -- its x-position and compute how many "move left" commands to issue.
            -- If the window lands in the wrong spot, try increasing this (0.2–0.3).
            if hidden.positionIdx then
                local savedId = hidden.windowId
                local savedIdx = hidden.positionIdx
                hs.timer.doAfter(0.15, function()
                    hs.execute(string.format("%s focus --window-id %s", aero, savedId))
                    local wsWinIds = getWorkspaceWindowIds(targetWs)
                    local sorted = sortedByX(wsWinIds)
                    local currentIdx = nil
                    for i, entry in ipairs(sorted) do
                        if entry.id == savedId then currentIdx = i; break end
                    end
                    if currentIdx and currentIdx > savedIdx then
                        for _ = 1, currentIdx - savedIdx do
                            hs.execute(aero .. " move left")
                        end
                    end
                end)
            end

        -- CASE 3: App not frontmost, no parked window — focus or create.
        else
            local onCurrentWs = not appConfig.summonHere
            if appConfig.summonHere and targetWindow then
                onCurrentWs = true
            end

            if window and onCurrentWs then
                if targetWindow then
                    targetWindow:focus()
                else
                    app:mainWindow():focus()
                end
            elseif appConfig.newWindowMenuItem then
                if appConfig.summonHere and window then
                    local beforeWindows = {}
                    for _, w in ipairs(app:allWindows()) do
                        beforeWindows[w:id()] = true
                    end

                    app:selectMenuItem(appConfig.newWindowMenuItem)
                    app:activate()

                    -- TUNABLE: Polls for the new window to appear. 20 retries at
                    -- 0.05s = up to 1s total. If the hotkey feels unresponsive after
                    -- creating a new window, the app may need more time — increase
                    -- retries. If hiding right after creation sometimes fails, the
                    -- window may not yet be registered with AeroSpace when you press
                    -- the hotkey again — try increasing the per-retry interval.
                    local retries = 20
                    local function checkNewWindow()
                        local newWinId = nil
                        for _, w in ipairs(app:allWindows()) do
                            if not beforeWindows[w:id()] then
                                newWinId = tostring(w:id())
                                break
                            end
                        end
                        if newWinId then
                            hs.execute(string.format("%s move-node-to-workspace --window-id %s %s", aero, newWinId, ws))
                            hs.execute(string.format("%s focus --window-id %s", aero, newWinId))
                        elseif retries > 0 then
                            retries = retries - 1
                            hs.timer.doAfter(0.05, checkNewWindow)
                        end
                    end
                    hs.timer.doAfter(0.05, checkNewWindow)
                else
                    app:selectMenuItem(appConfig.newWindowMenuItem)
                    app:activate()
                end
            elseif window then
                app:mainWindow():focus()
            end
        end
    else
        hs.application.open(appID)
    end
end
