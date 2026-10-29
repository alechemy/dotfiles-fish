# Pi from an iPhone

Termius connects to the Mac's ordinary OpenSSH server over Tailscale. `pi-mobile` runs Pi inside a separate tmux server on the Mac. Desktop cmux sessions keep their existing launch and restoration behavior.

This trial deliberately has no new session locking. Finish the current turn and **quit Pi before continuing the same conversation on the other device**. Disconnecting Termius or detaching tmux leaves Pi running. Do not resume that conversation elsewhere until its existing Pi process exits.

## Install the Mac configuration

The `pi-mobile` Stow package owns the launcher, mobile tmux configuration, and a mobile-only Pi extension. Homebrew already provides tmux and Pi. Normal dotfiles setup discovers the package automatically.

From the primary dotfiles checkout:

```sh
stow --restow --no-folding --ignore='__pycache__' --dir=stow --target="$HOME" pi-mobile
```

The mobile configuration sources `~/.config/tmux/tmux.conf`; the existing `tmux` package must also be active. No change to Pi's settings fragment or normal Fish startup is needed. Updated tmux configuration takes effect after all mobile Pi windows have exited and the mobile server restarts. To apply configuration edits without ending running sessions, use `tmux -L pi-mobile source-file ~/.config/tmux/pi-mobile.conf`. Extension edits take effect when Pi starts or runs `/reload`.

## Connect the phone

1. Install Tailscale on the iPhone, sign in to the same tailnet as the Mac, and connect. Keep the Mac awake and logged in for the trial.
2. In Termius, create a dedicated Ed25519 key through its Keychain/key-generation controls. Keep the private key local to the phone for this trial. Copy only its public key, beginning with `ssh-ed25519`.
3. Give that public key to the configuring Pi session and ask it to install a source-restricted entry in the Mac's `~/.ssh/authorized_keys`, preserving all existing entries. The required options are:

   ```text
   from="100.64.0.0/10,fd7a:115c:a1e0::/48",restrict,pty ssh-ed25519 PUBLIC_KEY termius-iphone
   ```

   The options allow this key through Tailscale source addresses, permit a terminal, and disable SSH forwarding and user RC execution. This still grants the macOS account's shell access. It does not restrict other authorized keys or close port 22 on other interfaces. Keep the real key and all device addresses outside Git.
4. Find the Mac's Tailscale address using the Tailscale menu-bar app. For its IPv4 address, run:

   ```sh
   /Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4
   ```

   Create a Termius host with that address, port `22`, your macOS short username from `id -un`, and the dedicated key. Use the Tailscale address rather than the Mac's LAN address.
5. Before accepting Termius's first host-key prompt, compare the displayed Ed25519 fingerprint against this command on the Mac:

   ```sh
   ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
   ```

6. Connect, then type `pi-mobile`. If the SSH shell cannot find it, use `~/.local/bin/pi-mobile`. This workflow does not require a paid startup-snippet feature.

The existing Tailscale GUI app stays installed. Do not enable `tailscale up --ssh`; the GUI variant does not provide a Tailscale SSH server. OpenSSH performs authentication here.

## Everyday commands

| Action | Command or keys |
| --- | --- |
| Open a directory-scoped picker in a new, independent mobile session | `pi-mobile` (or `pi-mobile resume`) |
| Choose a running mobile session to reconnect to | `pi-mobile attach` |
| List running mobile sessions without attaching | `pi-mobile list` |
| Show saved conversations from every directory in Pi's picker | `Tab` |
| Search the picker | Type a name or search text |
| Switch among running mobile windows | `Ctrl+B`, then `W` |
| Open another picker in the active conversation's directory | `Ctrl+B`, then `R` |
| Disconnect while leaving Pi running | `Ctrl+B`, then `D` |
| Submit a Pi prompt | `Ctrl+S` |
| Insert a newline | `Enter` |
| Finish this mobile Pi process | `/quit` |

Press and release the tmux prefix before the second key. Termius's extra keyboard keys provide Control. The launcher disables terminal software flow control so Ctrl+S reaches Pi. Cmd+Enter is not needed.

Run `cd /path/to/project` before `pi-mobile`. Each invocation opens Pi's saved-conversation picker for that directory in a new, independent tmux session. Separate Termius connections can navigate their own windows without switching each other's view. `pi-mobile resume` is an alias for the same behavior.

`pi-mobile attach` shows a numbered menu with each running session's active conversation name, project directory, window count, and attached/detached status. Type a menu number to reconnect, or press Enter to cancel. No session ID is needed. Reconnecting preserves the running Pi process. Choosing an entry marked `attached` shares that session's view with its existing connection.

Detaching leaves Pi running. Use `attach` to return to that process instead of selecting the same conversation in a new picker. Do not select a conversation already running in any mobile window or on desktop. Quitting Pi closes its window. When a session's last window exits, that tmux session ends.

The picker opens the saved conversation in its recorded working directory. Use `/name` on desktop for names you can recognize on the phone. Mobile tmux windows show the project and saved conversation name as of opening. The extension records the conversation directory for the reconnect menu and `Ctrl+B`, then `R`, including when you select another project through `Tab`. Before a conversation is selected, the shortcut uses the pane's launch directory. If Pi offers to use the current directory because the original directory is missing, cancel. The mobile extension also refuses work when the recorded directory is missing or differs from Pi's runtime directory.

## Switching devices

### Desktop to phone

1. Wait for the desktop turn to finish and enter `/quit` in that Pi conversation.
2. Connect in Termius, change to the project's directory, and run `pi-mobile`.
3. Select the saved conversation. To find one in another directory, press `Tab`. Check the project directory in Pi's footer before submitting work.

### Phone to desktop

1. Finish the mobile turn and enter `/quit`. Detaching is insufficient.
2. For a tracked Worktrunk task, run `wt pi open <branch>` from a native cmux shell in that repository. For other conversations, run `pi --resume` in cmux and select the conversation.
3. If Worktrunk reports a stale or ambiguous terminal binding, stop and inspect it using the [existing recovery workflow](cmux.md#recovery-and-limits). Do not bypass it by starting duplicate sessions.

For a new mobile task, use a separate tmux window and an existing appropriate worktree. Pi's `/new` starts a new conversation in the selected conversation's directory. `wt pi new` remains a desktop cmux command; the mobile launcher does not allocate or adopt worktrees.

## Desktop compatibility and limits

- The mobile server uses socket name `pi-mobile`. It does not reconfigure or stop the default tmux server.
- The launcher refuses an outer launch from a cmux terminal or inside another tmux session. It removes all `CMUX_*` variables before tmux starts and again before Pi starts, including variables left in a server environment.
- The extension is loaded explicitly only for mobile launches. It checks the original directory and names its exact tmux window. It does not register session ownership, acquire a lock, or change desktop Pi settings.
- Desktop Hunk feedback and cmux pane/browser operations are unavailable through the normal mobile integration. Do not issue raw cmux commands from mobile to operate a focused desktop workspace. Environment cleanup is not an OS sandbox; the same user account can still reach local services.
- Authentication that needs an unlocked 1Password app or an on-screen approval may need the Mac. SSH-launched programs may also encounter different macOS privacy permissions. Do not grant broad permissions merely to suppress a prompt.
- The Mac must remain reachable. Existing battery, lid, login, and FileVault behavior is unchanged. For the first trial, keep it connected to power with the lid open; remote access after reboot can require a local unlock.

## Bounded phone trial

After key enrollment, use one named, idle conversation for a five-minute trial:

1. Quit it on desktop and resume it through Termius. Confirm its directory and send one harmless prompt with Ctrl+S.
2. Detach with Ctrl+B then D, run `pi-mobile attach`, and select the conversation by its menu number. Confirm the same running conversation remains.
3. Quit mobile Pi and resume the same conversation on desktop. Confirm the phone turn is present.
4. With the dedicated key, try the Mac's LAN address once. Authentication should be refused. Return the Termius host to its Tailscale address.

Do not intentionally open two writers to test this trial. Revisit locking if actual device switching proves awkward or accidental overlap occurs.

## Local checks

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s scripts/tests -p test_pi_mobile.py
PI_PACKAGE_ROOT=/opt/homebrew/Cellar/pi-coding-agent/1.4.0/libexec/lib/node_modules/@earendil-works/pi-coding-agent node --test scripts/tests/test_pi_mobile.mjs
```

The Python tests use a unique `pi-mobile-test-*` socket, a temporary HOME, and a fake Pi executable. Cleanup stops only that test server. The extension tests use synthetic contexts and the installed Pi loader without model calls. Phone authentication and key delivery require the bounded trial above.

## References

- [Tailscale macOS variants](https://tailscale.com/docs/concepts/macos-variants)
- [Termius for iPhone](https://termius.com/free-ssh-client-for-iphone)
- [tmux manual](https://github.com/tmux/tmux/blob/master/tmux.1)
