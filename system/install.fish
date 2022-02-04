#!/usr/bin/env fish

# Make rm a little safer (have it prompt once when deleting
# more than three files or when deleting recursively).
abbr --add --global rm 'rm -I'
