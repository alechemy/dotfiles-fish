#!/usr/bin/env fish

# TO-DO: configure this to run only if `brew autoupdate status` reports
# that the autoupdate service is not currently running.

brew autoupdate start --upgrade
