#!/usr/bin/env bash
# Refresh the public snapshot of the in-progress `medium` run: regenerate the
# README status block, commit the current logs, and push.
#
# Designed to be run unattended from cron, which means:
#   - absolute paths only (cron's cwd and PATH are not a login shell's)
#   - it refuses to touch anything if the repo is not on the training branch,
#     so it can never commit half-finished work from another branch
#   - it stages ONLY logs/ and README.md, never `git add -A`; source edits in
#     progress are not swept into an automated commit
#   - every run appends to logs/sync_status.log, so a silent failure (expired
#     credentials, no network) is diagnosable after the fact
#
# Install:  crontab -l | { cat; echo "17 * * * * /home/system/projects/AuthLLM/scripts/sync_training_logs.sh"; } | crontab -
# Run now:  bash scripts/sync_training_logs.sh

set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO" || exit 1

BRANCH=124-million-training
PYTHON="$REPO/.venv/bin/python"
SYNC_LOG="$REPO/logs/sync_status.log"

log() { echo "[sync] $(date -Is) $*" >> "$SYNC_LOG"; }

CURRENT=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ "$CURRENT" != "$BRANCH" ]; then
    log "skip: on branch '$CURRENT', not '$BRANCH'"
    exit 0
fi

# Regenerate the status block. Exit 0 means it changed, 1 means the run has
# not advanced since last time -- in which case there is nothing to publish
# and a commit would be pure noise.
if ! "$PYTHON" "$REPO/scripts/update_training_status.py" >> "$SYNC_LOG" 2>&1; then
    log "status block unchanged; nothing to sync"
    exit 0
fi

git add logs README.md
if git diff --cached --quiet; then
    log "no staged changes; nothing to commit"
    exit 0
fi

STEP=$(git show :README.md | grep -oP 'step \*\*\K[0-9,]+' | head -1)
LOSS=$(git show :README.md | grep -oP '\*\*Training loss\*\* \| \K[0-9.]+' | head -1)
MSG="Training progress: step ${STEP:-?} (loss ${LOSS:-?})"

if ! git commit -q -m "$MSG" -m "Automated snapshot of the in-progress 124M run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"; then
    log "ERROR: commit failed"
    exit 1
fi
log "committed: $MSG"

# Push failures must be loud in the log but must not abort the loop -- the
# next hourly run will carry this commit plus the newer one.
if git push -q origin "$BRANCH" 2>>"$SYNC_LOG"; then
    log "pushed to origin/$BRANCH"
else
    log "ERROR: push failed (commit is local; next run will retry)"
    exit 1
fi
