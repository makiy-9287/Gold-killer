#!/usr/bin/env bash
# state/state.json එක `bot-state` branch එකේ (single commit) save/restore කරනවා.
# usage: scripts/state_sync.sh restore|save
set -euo pipefail
MODE="${1:?restore|save}"
BRANCH="bot-state"
FILE="state/state.json"
REMOTE="${REMOTE:-https://x-access-token:${GITHUB_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch_branch() {
  cd "$WORK" && git init -q . && git remote add origin "$REMOTE"
  git fetch -q --depth=1 origin "$BRANCH" 2>/dev/null
}

if [ "$MODE" = "restore" ]; then
  DEST="$(pwd)/$FILE"; mkdir -p "$(dirname "$DEST")"
  if (fetch_branch); then
    (cd "$WORK" && git show FETCH_HEAD:state.json) > "$DEST" 2>/dev/null || echo '{}' > "$DEST"
    echo "state restored"
  else
    [ -f "$DEST" ] || echo '{}' > "$DEST"
    echo "no bot-state branch yet - starting fresh"
  fi
elif [ "$MODE" = "save" ]; then
  SRC="$(pwd)/$FILE"
  [ -f "$SRC" ] || { echo "no state file"; exit 0; }
  cd "$WORK" && git init -q . && git remote add origin "$REMOTE"
  git config user.name "xauusd-bot"; git config user.email "bot@users.noreply.github.com"
  if git fetch -q --depth=1 origin "$BRANCH" 2>/dev/null; then
    git checkout -q -b "$BRANCH" FETCH_HEAD
  else
    git checkout -q --orphan "$BRANCH"
  fi
  cp "$SRC" state.json
  git add state.json
  if git diff --cached --quiet && git rev-parse -q --verify HEAD >/dev/null; then echo "state unchanged"; exit 0; fi
  if git rev-parse -q --verify HEAD >/dev/null; then git commit -q --amend -m "bot state" --reset-author
  else git commit -q -m "bot state"; fi
  git push -q --force origin "$BRANCH"
  echo "state saved"
fi
