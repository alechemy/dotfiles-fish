#!/usr/bin/env fish

sudo port install beets-full

set BEETS_HOME "$HOME/.config/beets"
mkdir -p "$BEETS_HOME"
mkdir -p "$HOME/.beets/_staging" "$HOME/.beets/_incoming"

ln -sf "$DOTFILES/beets/config.yaml" "$BEETS_HOME/config.yaml"
ln -sf "$DOTFILES/beets/nas.yaml" "$BEETS_HOME/nas.yaml"
ln -sf "$DOTFILES/beets/soundtrack-config.yaml" "$BEETS_HOME/soundtrack-config.yaml"
ln -sf "$DOTFILES/beets/genres.txt" "$BEETS_HOME/genres.txt"
ln -sf "$DOTFILES/beets/genres-tree.yaml" "$BEETS_HOME/genres-tree.yaml"
ln -sf "$DOTFILES/beets/MusicRefresh.scpt" "$BEETS_HOME/MusicRefresh.scpt"

if not grep -q "/-    auto_smb_media" /etc/auto_master
    echo "Adding autofs master entry…"
    echo "/-    auto_smb_media" | sudo tee -a /etc/auto_master >/dev/null
end

set SECRETS "$BEETS_HOME/secrets.yaml"
if test -f "$DOTFILES/beets/secrets.yaml"
    ln -sf "$DOTFILES/beets/secrets.yaml" "$SECRETS"
else if test -f "$DOTFILES/beets/secrets.example.yaml" -a ! -f "$SECRETS"
    cp "$DOTFILES/beets/secrets.example.yaml" "$SECRETS"
    echo "⚠️  Edit $SECRETS with your NAS credentials."
end

set NAS_USER (python3 - <<'PY'
import sys, yaml, os
p = os.path.expanduser(os.environ.get('SECRETS', sys.argv[1]))
try:
  print(yaml.safe_load(open(p))['nas']['user'])
except Exception:
  print('alec')
PY
"$SECRETS")

set TMP (mktemp)
sed "s/__NAS_USER__/$NAS_USER/g" "$DOTFILES/beets/autofs/auto_smb_media.template" >"$TMP"
sudo install -m 644 "$TMP" /etc/auto_smb_media
rm -f "$TMP"

sudo mkdir -p /Volumes/Media

sudo automount -vc

set NAS_PASS (python3 - <<'PY'
import sys, yaml, os, getpass
p = os.path.expanduser(os.environ.get('SECRETS', sys.argv[1]))
try:
  print(yaml.safe_load(open(p))['nas']['pass'])
except Exception:
  print("")
PY
"$SECRETS")

if test -z "$NAS_PASS"
    read -s -P "NAS password for $NAS_USER: " NAS_PASS
end

# Create/update Internet password item
security add-internet-password \
    -s AlecsVault.local \
    -r "smb " \
    -a "$NAS_USER" \
    -w "$NAS_PASS" \
    -D "Internet password" \
    ~/Library/Keychains/login.keychain-db >/dev/null

echo "✅ Beets + autofs setup complete."
echo "• Incoming:      $HOME/.beets/_incoming"
echo "• Staging:       $HOME/.beets/_staging"
echo "• NAS mount:     /Volumes/Media (autofs)"
