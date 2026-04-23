# Dropzone Action Info
# Name: Send to DEVONthink
# Description: Import dragged files into a specific DEVONthink group.
# Handles: Files
# Creator: Alec Custer
# URL: https://www.devontechnologies.com
# Events: Dragged
# SkipConfig: Yes
# RunsSandboxed: No
# Version: 1.0
# MinDropzoneVersion: 4.0
# UniqueID: 8294017365

DEVONTHINK_GROUP_UUID = "6B07BF0B-4DFE-44E7-A044-663C7FD0D212"

def dragged
  $dz.begin("Importing #{$items.count} file#{$items.count == 1 ? '' : 's'} into DEVONthink...")
  $dz.determinate(true)

  total = $items.count
  imported = 0

  $items.each_with_index do |file, index|
    escaped_path = file.gsub('"', '\\"')

    script = <<~APPLESCRIPT
      tell application id "DNtp"
        set targetGroup to get record with uuid "#{DEVONTHINK_GROUP_UUID}"
        if targetGroup is missing value then
          error "Group not found for UUID: #{DEVONTHINK_GROUP_UUID}"
        end if
        set theResult to import "#{escaped_path}" to targetGroup
      end tell
    APPLESCRIPT

    result = `/usr/bin/osascript -e '#{script.gsub("'", "'\\''")}'  2>&1`

    if $?.success?
      imported += 1
    else
      $dz.error("Import Failed", "Could not import #{File.basename(file)}:\n#{result}")
      return
    end

    $dz.percent(((index + 1) * 100) / total)
  end

  $dz.finish("Imported #{imported} file#{imported == 1 ? '' : 's'} to DEVONthink")
  $dz.url(false)
end
