require('apps')

--[[---------------
---- CONSTANTS ----
-------------------]]
hyper = {"rightcmd", "rightoption", "rightctrl", "rightshift"}

--[[----------------------
---- UTILITY BINDINGS ----
--------------------------]]

-- hyperkey + "L" = Lock screen
hs.hotkey.bind(hyper, "L", function()
  hs.caffeinate.lockScreen()
end)

-- hyperkey + "0" = reload hammerspoon config
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
    ["name"] = "Visual Studio Code - Insiders",
    ["bundleID"] = "com.microsoft.VSCodeInsiders",
    ["newWindowMenuItem"] = {"File", "New Window"}
  },
  ["D"] = {
    ["name"] = "DEVONthink 3",
    ["newWindowMenuItem"] = {"File", "New Window", "Notebox"}
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
  ["K"] = {
    ["name"] = "kitty",
    ["newWindowMenuItem"] = {"Shell", "New OS window"}
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
  ["P"] = {
    ["name"] = "Pins"
  },
  ["R"] = {
    ["name"] = "Reeder",
    ["newWindowMenuItem"] = {"Window", "Reeder"}
  },
  ["S"] = {
    ["name"] = "Slack",
    ["newWindowMenuItem"] = {"File", "Workspace", "Xperi"}
  },
--[[["T"] = {
    ["name"] = "Tot"
  }]]
--[[["V"] = {
    ["name"] = "Maccy"
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
  },
  ["S"] = {
    ["name"] = "Safari",
    ["newWindowMenuItem"] = {"File", "New Window"}
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

hs.urlevent.bind('fnv', function() hs.eventtap.keyStrokes(hs.pasteboard.getContents()) end)

-- This will just log the event; useful for debugging...
hs.urlevent.bind("karabiner", function(eventName, params)
  print("Event: "..eventName)
  print(hs.inspect(params))
end)

hs.notify.new({title="Hammerspoon", informativeText="Config loaded"}):send()
