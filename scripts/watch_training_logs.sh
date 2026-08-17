#!/usr/bin/env bash
# Fallback scheduler for sync_training_logs.sh, for when installing a cron
# entry is not possible. Cron is the better mechanism -- it survives a reboot
# and this does not -- so prefer:
#
#   17 * * * * /home/system/projects/AuthLLM/scripts/sync_training_logs.sh
#
# Usage: nohup bash scripts/watch_training_logs.sh > /dev/null 2>&1 &
#
# Exits on its own once the run is finished and the final state has been
# published, so it does not linger for days after the work it was watching.

set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO" || exit 1

INTERVAL=${INTERVAL:-3600}
SYNC_LOG="$REPO/logs/sync_status.log"

echo "[watch] $(date -Is) started; syncing every ${INTERVAL}s" >> "$SYNC_LOG"

while true; do
    bash "$REPO/scripts/sync_training_logs.sh"

    # Stop once training has finished AND the final status has been pushed --
    # judged by the training process being gone and the working tree clean.
    if ! pgrep -f "scripts/train\.py" > /dev/null; then
        if [ -z "$(git status --porcelain logs README.md)" ] && \
           [ -z "$(git log origin/124-million-training..HEAD --oneline 2>/dev/null)" ]; then
            echo "[watch] $(date -Is) training stopped and everything published; exiting" >> "$SYNC_LOG"
            exit 0
        fi
    fi

    sleep "$INTERVAL"
done
