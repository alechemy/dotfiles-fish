require('apps')

-- Obsidian sidebar hider; no longer needed but left as an example
-- local obsidianSidebarHider = "~/Developer/hide-obsidian-sidebar.sh"
-- local obsidianWatcher = hs.window.filter.new(false):setAppFilter("Obsidian")
-- obsidianWatcher:subscribe(hs.window.filter.windowTitleChanged, function(window, appName, event)
--     hs.execute(obsidianSidebarHider)
-- end)

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

--[[--------------------------------
---- HYPER APPLICATION BINDINGS ----
------------------------------------]]

-- A commented out config here is (usually) a note-to-self, indicating that the binding
-- is set within the preferences of the respective app.
hyper_bindings = {
  ["A"] = {
    ["name"] = "Anki"
    },
  ["B"] = {
    ["name"] = "chromium",
    ["bundleID"] = "org.chromium.Chromium",
    ["newWindowMenuItem"] = {"File", "New Window"},
    -- ["summonHere"] = true
  },
  ["C"] = {
    ["name"] = "VSCodium",
    ["bundleID"] = "com.vscodium",
    ["newWindowMenuItem"] = {"File", "New Window"}
  },
  ["D"] = {
    ["name"] = "DEVONthink",
    ["bundleID"] = "com.devon-technologies.think",
    ["newWindowMenuItem"] = {"File", "New Window", "Lorebook"}
  },
  ["E"] = {
    ["name"] = "Mail",
    ["newWindowMenuItem"] = {"Window", "Message Viewer"}
  },
  --[[
  ["F"] = {
    ["name"] = "Fantastical"
  }]]
  ["G"] = {
    ["name"] = "Things",
    ["bundleID"] = "com.culturedcode.ThingsMac",
    ["newWindowMenuItem"] = {"File", "New Things Window"}
  },
  ["T"] = {
    ["name"] = "Ghostty",
    ["bundleID"] = "com.mitchellh.ghostty",
    ["newWindowMenuItem"] = {"File", "New Window"},
    ["summonHere"] = true
  },
  ["N"] = {
    ["name"] = "Feishin",
    ["bundleID"] = "org.jeffvli.feishin",
  },
  ["O"] = {
    ["name"] = "Obsidian",
    ["bundleID"] = "md.obsidian",
  },
  ["S"] = {
    ["name"] = "Safari",
    ["newWindowMenuItem"] = {"File", "New Window"}
  },
  ["W"] = {
    ["name"] = "Microsoft Teams"
  },
--[[
  ["W"] = {
    ["name"] = "Run 'Copy URL and title of current web page in Markdown format' KM macro"
  }]]
--[[
  ["X"] = {
    ["name"] = "Clip to DEVONthink"
  }]]
  ["Z"] = {
    ["name"] = "Zed Preview",
    ["bundleID"] = "dev.zed.Zed-Preview",
    ["newWindowMenuItem"] = {"File", "New Window"}
  },
}

-- Note: These bindings work by registering Karabiner Elements events
-- Therefore, a new shyper binding requires a corresponding addition to karabiner.edn!
shyper_bindings = {
  ["E"] = {
    ["name"] = "ears",
    ["newWindowMenuItem"] = {"Window", "Main Window"}
  },
  ["F"] = {
    ["name"] = "finder",
    ["newWindowMenuItem"] = {"File", "New Finder Window"}
  },
  ["M"] = {
    ["name"] = "messages",
    ["newWindowMenuItem"] = {"Window", "Messages"}
  },
  ["O"] = {
    ["name"] = "outlook",
    ["bundleID"] = "com.apple.Safari.WebApp.5353ED03-6BF4-40DB-A16D-2146FB4CD7A3",
    ["newWindowMenuItem"] = {"File", "New", "Main Window"}
  },
}

for key, app in pairs(hyper_bindings) do
  --[[ Some apps, like Music and Keep It, launch in alternate
  modes if you're holding down the Option key. So for
  these, we'll release the modifier keys before launching. ]]
  if app.triggerOnRelease then
    hs.hotkey.bind(hyper, key, nil, function()
      hs.eventtap.keyStroke({}, "alt", 0)
      openOrHideApp(app)
    end)
  else
    hs.hotkey.bind(hyper, key, function()
      openOrHideApp(app)
    end)
  end
end

-- These are emitted by Karabiner-Elements
for key, app in pairs(shyper_bindings) do
  if app.triggerOnRelease then
    hs.urlevent.bind(app.name, function()
      hs.eventtap.keyStroke({}, "alt", 0)
      openOrHideApp(app)
    end)
  else
    hs.urlevent.bind(app.name, function()
      openOrHideApp(app)
    end)
  end
end

-- AeroSpace resize with key repeat (hyper+minus / hyper+equal)
local function aerospaceResize(delta)
  return function() hs.execute("/opt/homebrew/bin/aerospace resize smart " .. delta) end
end
hs.hotkey.bind(hyper, "-", aerospaceResize("-50"), nil, aerospaceResize("-50"))
hs.hotkey.bind(hyper, "=", aerospaceResize("+50"), nil, aerospaceResize("+50"))

--[[------------------------
---- URL EVENT BINDINGS ----
----------------------------]]

-- Shyper-D => Dropzone KM Macro
hs.urlevent.bind("dropzone", function(eventName, params)
    local kmUrl = "kmtrigger://macro=2574A57C-F186-4256-BFBD-D770BA189E33"
    hs.urlevent.openURL(kmUrl)
end)

-- Shyper-L => Lock screen
hs.urlevent.bind('Lock', function() hs.caffeinate.lockScreen() end)

-- Shyper-V => Paste by individually typing each character. Useful where regular pasting is disabled.
hs.urlevent.bind('fnv', function() hs.eventtap.keyStrokes(hs.pasteboard.getContents()) end)

-- Binds 'hammerspoon://debug' to a log of said event. (Useful for, you guessed it, debugging.)
hs.urlevent.bind("debug", function(eventName, params)
  print("Event: "..eventName)
  print(hs.inspect(params))
end)

hs.notify.new({title="Hammerspoon", informativeText="Config loaded"}):send()
