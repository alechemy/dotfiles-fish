#!/usr/bin/env fish

mkdir -p "$HOME/Library/LaunchAgents/"
set LAUNCHAGENTS_HOME "$HOME/Library/LaunchAgents/"

ln -sf "$DOTFILES/macos/time-machine/com.alec.DailyTimeMachineBackup.plist" "$LAUNCHAGENTS_HOME/com.alec.DailyTimeMachineBackup.plist"

# Wake daily at 1am
sudo pmset repeat wake MTWRFSU 00:01:00

# Install LaunchAgent that will back up daily at 1:05am
set userId (launchctl manageruid)

if launchctl list | grep -q com.alec.DailyTimeMachineBackup
    launchctl bootout gui/501 $LAUNCHAGENTS_HOME/com.alec.DailyTimeMachineBackup.plist
end

launchctl bootstrap gui/$userId/ $LAUNCHAGENTS_HOME/com.alec.DailyTimeMachineBackup.plist
