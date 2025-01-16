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

-- A commented out config here is a note-to-self, indicating that the binding
-- is set within the preferences of the respective app.
hyper_bindings = {
  ["C"] = {
    ["name"] = "VSCodium",
    ["bundleID"] = "com.vscodium",
    ["newWindowMenuItem"] = {"File", "New Window"}
  },
  ["D"] = {
    ["name"] = "DEVONthink 3",
    ["bundleID"] = "com.devon-technologies.think3",
    ["newWindowMenuItem"] = {"File", "New Window", "My Database"}
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
  ["I"] = {
    ["name"] = "IntelliJ IDEA"
  },
  ["K"] = {
    ["name"] = "kitty",
    ["newWindowMenuItem"] = {"Shell", "New OS Window"}
  },
  ["M"] = {
    ["name"] = "Music",
    ["bundleID"] = "com.apple.Music",
    ["triggerOnRelease"] = true,
    ["newWindowMenuItem"] = {"Window", "Music"}
  },
  ["O"] = {
    ["name"] = "Microsoft Outlook",
    ["newWindowMenuItem"] = {"File", "New", "Main Window"}
  },
  ["R"] = {
    ["name"] = "Reeder",
    ["newWindowMenuItem"] = {"Window", "Reeder"}
  },
  ["S"] = {
    ["name"] = "Slack",
    ["newWindowMenuItem"] = {"File", "Workspace", "Xperi"}
  },
--[[
  ["T"] = {
    ["name"] = "Tot"
  }]]
--[[
  ["W"] = {
    ["name"] = "Run 'Copy URL and title of current web page in Markdown format' KM macro"
  }]]
--[[
  ["X"] = {
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
  },
  ["O"] = {
    ["name"] = "Obsidian",
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

--[[------------------------
---- URL EVENT BINDINGS ----
----------------------------]]

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
