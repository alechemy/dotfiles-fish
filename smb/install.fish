#!/usr/bin/env fish
set SHARE_NAME "Media"
set NAS_PATH "://alec@AlecsVault.local/Media"
set MOUNT_OPTS "-fstype=smbfs,soft,noowners,nosuid"
set NSMB_CONF "/etc/nsmb.conf"
set SYNTHETIC_CONF "/etc/synthetic.conf"
set AUTO_MAP_FILE "/etc/auto_smb"
set AUTO_MASTER "/etc/auto_master"

set synthetic_line "Volumes/$SHARE_NAME "System/Volumes/Data/Volumes/$SHARE_NAME
set automap_line "$SHARE_NAME $MOUNT_OPTS $NAS_PATH"
set automaster_line "/System/Volumes/Data/Volumes $AUTO_MAP_FILE"

if test (id -u) -ne 0
    echo "This script needs root privileges to modify files in /etc."
    sudo (status --current-filename) $argv
    exit $status
end

# 1. Configure /etc/nsmb.conf
sudo ln -sf "$DOTFILES/smb/nsmb.conf" $NSMB_CONF
echo "Configured $NSMB_CONF"

# 2. Configure /etc/synthetic.conf
if grep -qF "$synthetic_line" $SYNTHETIC_CONF
    echo "Already configured $SYNTHETIC_CONF"
else
    printf "%s\n" "$synthetic_line" >> $SYNTHETIC_CONF
    echo "Added entry to $SYNTHETIC_CONF"
end

# 3. Configure /etc/auto_smb
printf "%s\n" "$automap_line" > $AUTO_MAP_FILE
echo "Configured $AUTO_MAP_FILE"

# 4. Configure /etc/auto_master
if grep -qF -- "$automaster_line" $AUTO_MASTER
    echo "Already configured $AUTO_MASTER"
else
    printf "%s\n" "$automaster_line" >> $AUTO_MASTER
    echo "Added entry to $AUTO_MASTER"
end
