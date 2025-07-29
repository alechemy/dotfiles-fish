require('apps')

-- Obsidian sidebar hider; no longer needed but left as an example
-- local obsidianSidebarHider = "/Users/alec.custer/Developer/hide-obsidian-sidebar.sh"
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
  ["C"] = {
    ["name"] = "VSCodium",
    ["bundleID"] = "com.vscodium",
    ["newWindowMenuItem"] = {"File", "New Window"}
  },
  ["D"] = {
    ["name"] = "DEVONthink",
    ["bundleID"] = "com.devon-technologies.think",
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
  ["S"] = {
    ["name"] = "Safari",
    ["newWindowMenuItem"] = {"File", "New Window"}
  },
  ["T"] = {
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
  -- ["Z"] = {
  --   ["name"] = "zoom.us",
  --   ["newWindowMenuItem"] = {"zoom.us", "About Zoom"}
  -- },
}

-- Note: These bindings work by registering Karabiner Elements events
-- Therefore, a new shyper binding requires a corresponding addition to karabiner.json!
shyper_bindings = {
  ["C"] = {
    ["name"] = "Chromium",
    ["newWindowMenuItem"] = {"File", "New Window"}
  },
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
    ["bundleID"] = "md.obsidian",
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

-- Rectangle "Fill" macro:
-- Checks whether a window is on the left or right side of the screen. If the former,
-- sends "fill left" to Rectangle, and "fill right" for the latter.
hs.hotkey.bind(hyper, 'space', function()
  local win = hs.window.frontmostWindow() ; if not win then return end
  local scr = win:screen():frame()
  local cx  = win:frame().x + win:frame().w / 2
  local mid = scr.x + scr.w / 2

  if cx < mid then
      hs.execute('open -g "rectangle-pro://execute-action?name=fill-left"')
  else
      hs.execute('open -g "rectangle-pro://execute-action?name=fill-right"')
  end
end)

local function sendVscode(mods, key)
  hs.eventtap.keyStroke(mods, key)
end

local function withVSCode(fn)
  return function()
    local front = hs.application.frontmostApplication()
    if not front or front:name() ~= "VSCodium" then return end
    fn()
  end
end


-- VSCodium editor manipulation
-- If app occupies >50% screen width, alt+return splits horizontally
-- Otherwise, splits vertically. Add shift key to move to previous split
hs.hotkey.bind({"alt"}, "return", withVSCode(function()
  local win = hs.window.frontmostWindow()
  local sf, wf = win:screen():frame(), win:frame()
  if wf.w < sf.w/2 then
    sendVscode({"ctrl","alt","cmd"}, "down")
  else
    sendVscode({"ctrl","alt","cmd"}, "right")
  end
end))

hs.hotkey.bind({"alt","shift"}, "return", withVSCode(function()
  local win = hs.window.frontmostWindow()
  local sf, wf = win:screen():frame(), win:frame()
  if wf.w < sf.w/2 then
    sendVscode({"ctrl","alt","cmd"}, "up")
  else
    sendVscode({"ctrl","alt","cmd"}, "left")
  end
end))

--[[------------------------
---- URL EVENT BINDINGS ----
----------------------------]]

-- Shyper-V => Paste by individually typing each character. Useful where regular pasting is disabled.
hs.urlevent.bind('fnv', function() hs.eventtap.keyStrokes(hs.pasteboard.getContents()) end)

-- Shyper-R => New Item in Due
hs.urlevent.bind('NewDueTimer', function() hs.eventtap.keyStroke({'ctrl', 'cmd', 'alt', 'shift'}, '3') end)

-- Shyper-L => Lock screen
hs.urlevent.bind('Lock', function() hs.caffeinate.lockScreen() end)

-- Binds 'hammerspoon://debug' to a log of said event. (Useful for, you guessed it, debugging.)
hs.urlevent.bind("debug", function(eventName, params)
  print("Event: "..eventName)
  print(hs.inspect(params))
end)

hs.notify.new({title="Hammerspoon", informativeText="Config loaded"}):send()

