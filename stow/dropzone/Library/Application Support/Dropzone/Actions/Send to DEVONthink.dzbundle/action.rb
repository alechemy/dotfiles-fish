# Dropzone Action Info
# Name: Send to DEVONthink
# Description: Import dragged files into a specific DEVONthink group.
# Handles: Files
# Creator: Alec Custer
# URL: https://www.devontechnologies.com
# Events: Dragged
# SkipConfig: Yes
# RunsSandboxed: No
# Version: 1.1
# MinDropzoneVersion: 4.0
# UniqueID: 8294017365

require 'open3'

DEVONTHINK_GROUP_UUID = "6B07BF0B-4DFE-44E7-A044-663C7FD0D212"

# AppleScript receives the file path as argv[1] rather than via string
# interpolation. The previous version escaped only double quotes in the
# filename before interpolating into the AppleScript source, then escaped
# only single quotes when wrapping the whole thing in `osascript -e '...'`
# via backticks — filenames containing backslashes (or newlines, etc.)
# could still break out of either layer and execute unintended AppleScript
# or shell. Open3.capture3 with the path as a separate argv element avoids
# both layers entirely; only the constant UUID is interpolated.
SCRIPT = <<~APPLESCRIPT
  on run argv
    set filePath to item 1 of argv
    tell application id "DNtp"
      set targetGroup to get record with uuid "#{DEVONTHINK_GROUP_UUID}"
      if targetGroup is missing value then
        error "Group not found for UUID: #{DEVONTHINK_GROUP_UUID}"
      end if
      set theResult to import filePath to targetGroup
    end tell
  end run
APPLESCRIPT

def dragged
  $dz.begin("Importing #{$items.count} file#{$items.count == 1 ? '' : 's'} into DEVONthink...")
  $dz.determinate(true)

  total = $items.count
  imported = 0

  $items.each_with_index do |file, index|
    stdout, stderr, status = Open3.capture3("/usr/bin/osascript", "-e", SCRIPT, file)

    if status.success?
      imported += 1
    else
      diagnostic = [stdout, stderr].reject { |s| s.nil? || s.empty? }.join("\n").strip
      $dz.error("Import Failed", "Could not import #{File.basename(file)}:\n#{diagnostic}")
      return
    end

    $dz.percent(((index + 1) * 100) / total)
  end

  $dz.finish("Imported #{imported} file#{imported == 1 ? '' : 's'} to DEVONthink")
  $dz.url(false)
end
