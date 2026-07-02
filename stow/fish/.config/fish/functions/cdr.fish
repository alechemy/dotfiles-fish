function cdr -d "cd to the top level directory of the git repository"
  set -l root (git rev-parse --show-toplevel)
  and cd $root
end
