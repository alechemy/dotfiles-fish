#!/usr/bin/env bash
# init-wiki.sh
#
# Scaffolds the LLM Wiki directory at ~/Wiki.
# Idempotent — skips files that already exist.
#
# Usage:
#   ./scripts/init-wiki.sh              # create ~/Wiki
#   ./scripts/init-wiki.sh /path/to/dir # create at custom location

set -euo pipefail

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIKI_DIR="${1:-$HOME/Wiki}"
TEMPLATE="$DOTFILES/devonthink/wiki-claude-md-template.md"

info() { printf "\r  [ \033[00;34m..\033[0m ] %s\n" "$1"; }
success() { printf "\r\033[2K  [ \033[00;32mOK\033[0m ] %s\n" "$1"; }

info "Initializing wiki at $WIKI_DIR"

# Create directory structure
mkdir -p "$WIKI_DIR/raw" "$WIKI_DIR/wiki/sources" "$WIKI_DIR/wiki/entities" \
         "$WIKI_DIR/wiki/concepts" "$WIKI_DIR/wiki/reading" "$WIKI_DIR/wiki/synthesis"

# Copy CLAUDE.md template
if [ ! -f "$WIKI_DIR/CLAUDE.md" ]; then
    cp "$TEMPLATE" "$WIKI_DIR/CLAUDE.md"
    success "Created CLAUDE.md"
else
    info "CLAUDE.md already exists, skipping"
fi

# Initialize index.md
if [ ! -f "$WIKI_DIR/wiki/index.md" ]; then
    cat > "$WIKI_DIR/wiki/index.md" << 'EOF'
# Wiki Index

## Sources

## Entities

## Concepts

## Synthesis
EOF
    success "Created wiki/index.md"
else
    info "wiki/index.md already exists, skipping"
fi

# Initialize log.md
if [ ! -f "$WIKI_DIR/wiki/log.md" ]; then
    cat > "$WIKI_DIR/wiki/log.md" << 'EOF'
# Wiki Log
EOF
    success "Created wiki/log.md"
else
    info "wiki/log.md already exists, skipping"
fi

# Initialize skipped.md
if [ ! -f "$WIKI_DIR/wiki/skipped.md" ]; then
    cat > "$WIKI_DIR/wiki/skipped.md" << 'EOF'
# Skipped Sources

UUIDs of raw/ files intentionally not ingested. Checked during ingest to avoid re-processing.

Format: `- UUID — reason`
EOF
    success "Created wiki/skipped.md"
else
    info "wiki/skipped.md already exists, skipping"
fi

# Initialize git repo
if [ ! -d "$WIKI_DIR/.git" ]; then
    read -p "  ? Initialize git repository in $WIKI_DIR? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        git -C "$WIKI_DIR" init
        cat > "$WIKI_DIR/.gitignore" << 'EOF'
.DS_Store
EOF
        git -C "$WIKI_DIR" add -A
        git -C "$WIKI_DIR" commit -m "Initial wiki scaffold"
        success "Git repository initialized"
    fi
fi

success "Wiki initialized at $WIKI_DIR"
echo ""
echo "  Next steps:"
echo "    1. Create 'WikiExported' (Boolean) in DEVONthink → Settings → Data → Custom Metadata"
echo "    2. Create the 'Export: Wiki Raw' smart rule (see devonthink/README.md)"
echo "    3. Open Claude Code in $WIKI_DIR and run: ingest new files"
