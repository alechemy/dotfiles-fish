cask "feishin" do
  version "1.13.0"
  sha256 "967d6476ae3a06549798f466a64aeeefc39408dcfe271cb753b4c67d8b384b69"

  url "https://github.com/jeffvli/feishin/releases/download/v#{version}/Feishin-#{version}-mac-arm64.dmg",
      verified: "github.com/jeffvli/feishin/"
  name "Feishin"
  desc "Desktop client for Jellyfin, Navidrome, and Subsonic music servers"
  homepage "https://github.com/jeffvli/feishin"

  livecheck do
    url :url
    strategy :github_latest
  end

  depends_on macos: :big_sur

  app "Feishin.app"

  # Feishin's macOS builds are unsigned and unnotarized, so Homebrew's default
  # quarantine attribute makes Gatekeeper block the first launch. Strip it once
  # the app is in place so it opens without the "damaged / unidentified
  # developer" prompt.
  postflight do
    system_command "/usr/bin/xattr",
                   args: ["-dr", "com.apple.quarantine", "#{appdir}/Feishin.app"]
  end

  zap trash: [
    "~/Library/Application Support/Feishin",
    "~/Library/Caches/org.jeffvli.feishin",
    "~/Library/Caches/org.jeffvli.feishin.ShipIt",
    "~/Library/Logs/Feishin",
    "~/Library/Preferences/org.jeffvli.feishin.plist",
    "~/Library/Saved Application State/org.jeffvli.feishin.savedState",
  ]
end
