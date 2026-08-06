# Android SDK — installed per-machine (android-commandlinetools cask); inert when absent
if test -d $HOME/Library/Android/sdk
    set -gx ANDROID_HOME $HOME/Library/Android/sdk
    fish_add_path $ANDROID_HOME/platform-tools $ANDROID_HOME/emulator
end
