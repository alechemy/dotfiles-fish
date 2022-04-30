require('apps')

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

-- A commented out config here usually indicates that the binding is set within
-- the preferences of the respective app.
hyper_bindings = {
  ["C"] = {
    ["name"] = "Code - Insiders",
    ["bundleID"] = "com.microsoft.VSCodeInsiders",
    ["newWindowMenuItem"] = {"File", "New Window"}
  },
  ["D"] = {
    ["name"] = "DEVONthink 3",
    ["newWindowMenuItem"] = {"File", "New Window", "My Database"}
  },
  ["E"] = {
    ["name"] = "Mail",
    ["newWindowMenuItem"] = {"Window", "Message Viewer"}
  },
--[[["F"] = {
    ["name"] = "Fantastical"
  }]]
  ["G"] = {
    ["name"] = "Things",
    ["bundleID"] = "com.culturedcode.ThingsMac",
    ["newWindowMenuItem"] = {"File", "New Things Window"}
  },
--[[["H"] = {
    ["name"] = "HazeOver"
  }]]
  ["K"] = {
    ["name"] = "kitty",
    ["newWindowMenuItem"] = {"Shell", "New OS Window"}
  },
  ["L"] = {
    ["name"] = "Logseq"
  },
  ["M"] = {
    ["name"] = "Music",
    ["triggerOnRelease"] = true,
    ["newWindowMenuItem"] = {"Window", "Music"}
  },
  ["N"] = {
    ["name"] = "Nova",
    ["newWindowMenuItem"] = {"Window", "Launcher"}
  },
  ["O"] = {
    ["name"] = "Mimestream",
    ["newWindowMenuItem"] = {"Window", "Main Window"}
  },
  ["R"] = {
    ["name"] = "Reeder",
    ["newWindowMenuItem"] = {"Window", "Reeder"}
  },
  ["S"] = {
    ["name"] = "Slack",
    ["newWindowMenuItem"] = {"File", "Workspace", "Condé Nast"}
  },
--[[["T"] = {
    ["name"] = "Tot"
  }]]
  --[[["X"] = {
    ["name"] = "Clip to DEVONthink"
  }]]
  ["Z"] = {
    ["name"] = "zoom.us",
    ["newWindowMenuItem"] = {"zoom.us", "About Zoom"}
  },
}

shyper_bindings = {
  ["F"] = {
    ["name"] = "Finder",
    ["newWindowMenuItem"] = {"File", "New Finder Window"}
  },
  ["M"] = {
    ["name"] = "Messages",
    ["newWindowMenuItem"] = {"Window", "Messages"}
  }
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

--[[------------------------
---- URL EVENT BINDINGS ----
----------------------------]]

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

-- Shyper-V => Paste by individually typing each character. Useful where regular pasting is disabled.
hs.urlevent.bind('fnv', function() hs.eventtap.keyStrokes(hs.pasteboard.getContents()) end)

-- Shyper-L => Lock screen
hs.urlevent.bind('Lock', function() hs.caffeinate.lockScreen() end)

-- Binds 'hammerspoon://debug' to a log of said event. (Useful for, you guessed it, debugging.)
hs.urlevent.bind("debug", function(eventName, params)
  print("Event: "..eventName)
  print(hs.inspect(params))
end)

hs.notify.new({title="Hammerspoon", informativeText="Config loaded"}):send()
