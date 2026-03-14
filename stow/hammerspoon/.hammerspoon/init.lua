--[[---------------
---- CONSTANTS ----
-------------------]]
hyper = {"rightcmd", "rightoption", "rightctrl", "rightshift"}

--[[----------------------
---- UTILITY BINDINGS ----
--------------------------]]

-- hyperkey + "0" => reload hammerspoon config
hs.hotkey.bind(hyper, "0", function()
  hs.reload()
end)

-- Uncomment these commands to print out the running apps' titles and bundle IDs. Useful when creating new shortcuts.
--   hs.fnutils.each(hs.application.runningApplications(), function(app) print(app:title()) end)
--   hs.fnutils.each(hs.application.runningApplications(), function(app) print(app:bundleID()) end)
-- Alternatively, in terminal:
--   osascript -e 'id of app "Name of App"'

-- AeroSpace resize with key repeat (hyper+minus / hyper+equal)
local function aerospaceResize(delta)
  return function()
    hs.task.new("/opt/homebrew/bin/aerospace", function() end, {"resize", "smart", delta}):start()
  end
end
hs.hotkey.bind(hyper, "-", aerospaceResize("-50"), nil, aerospaceResize("-50"))
hs.hotkey.bind(hyper, "=", aerospaceResize("+50"), nil, aerospaceResize("+50"))

--[[------------------------
---- URL EVENT BINDINGS ----
----------------------------]]

-- Shyper-V => Paste by individually typing each character. Useful where regular pasting is disabled.
hs.urlevent.bind('fnv', function() hs.eventtap.keyStrokes(hs.pasteboard.getContents()) end)

-- App launchers routed from Karabiner
hs.urlevent.bind("ears", function() hs.application.launchOrFocus("Ears") end)
hs.urlevent.bind("finder", function() hs.application.launchOrFocus("Finder") end)

-- hammerspoon://reload => reload config (used by reload_wm fish function)
hs.urlevent.bind("reload", function() hs.reload() end)

-- Binds 'hammerspoon://debug' to a log of said event. (Useful for, you guessed it, debugging.)
hs.urlevent.bind("debug", function(eventName, params)
  print("Event: "..eventName)
  print(hs.inspect(params))
end)

hs.notify.new({title="Hammerspoon", informativeText="Config loaded"}):send()
