function openOrHideApp(appConfig)
    -- bundleID is more reliable, so use it if provided
    local appID = appConfig.bundleID or appConfig.name
    local app = hs.application.get(appID)

    -- App is open. Let's decide what to do with it.
    if app and app:isRunning() then
        local window = app:mainWindow()
        -- If window exists and it is frontmost, hide it.
        if window and app:isFrontmost() then
            app:hide()
        -- App is not frontmost, so try to show it.
        else
            if window then
                -- Show the app's window if one already exists.
                app:mainWindow():focus()
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
