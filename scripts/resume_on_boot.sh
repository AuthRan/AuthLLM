#!/usr/bin/env bash
# Restart an unfinished `medium` run after a reboot.
#
# A reboot on 2026-08-17 killed the run at step 14,000 and nothing brought it
# back; it sat dead until someone noticed and relaunched by hand. The launcher
# already knows how to resume from the newest checkpoint -- what was missing is
# anything to invoke it once the machine comes back up.
#
# Safe to run unconditionally at boot. It exits without doing anything if:
#   - there is no checkpoint directory yet (nothing to resume)
#   - the newest checkpoint is already at max_steps (the run finished)
#   - training is somehow already running (a second copy would corrupt logs
#     and fight over the GPU)
#
# Install (both lines -- the @reboot resume and the hourly status sync):
#   crontab -l 2>/dev/null | {
#       cat
#       echo "@reboot /home/system/projects/AuthLLM/scripts/resume_on_boot.sh"
#       echo "17 * * * * /home/system/projects/AuthLLM/scripts/sync_training_logs.sh"
#   } | crontab -
#
# Verify:  crontab -l

set -uo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO" || exit 1

CKPT_DIR="${CKPT_DIR:-$REPO/checkpoints/medium}"
CONFIG="${CONFIG:-$REPO/configs/train/fineweb_2x2080ti.yaml}"
BOOT_LOG="$REPO/logs/resume_on_boot.log"

# At boot this may run before anything else has created logs/, and both the
# lock and the log below need it to exist.
mkdir -p "$REPO/logs"

log() { echo "[resume] $(date -Is) $*" >> "$BOOT_LOG"; }

# GPU 1 is thermally limited and DDP runs at the slower card's pace, so this
# run is deliberately single-card. Kept here so a boot-time resume does not
# silently come back up in a slower configuration than it went down in.
export GPUS="${GPUS:-0}"
export NPROC="${NPROC:-1}"

[ -d "$CKPT_DIR" ] || { log "no checkpoint dir; nothing to resume"; exit 0; }

# Test the supervisor's lock rather than matching on command lines. `pgrep -f`
# matches any process whose *arguments* contain the pattern -- including a shell
# that merely mentions this script's name -- so it reports "already running"
# when nothing is, and this script then declines to do the one thing it exists
# to do. Failing closed is the worst outcome here.
exec 8>"$REPO/logs/.training.lock"
if ! flock -n 8; then
    log "supervisor holds the training lock; leaving it alone"
    exit 0
fi
flock -u 8          # only testing -- the supervisor takes its own lock
exec 8>&-           # and must not inherit this fd

newest=$(ls "$CKPT_DIR"/step_*.pt 2>/dev/null \
    | sed -E 's/.*step_([0-9]+)\.pt/\1/' | sort -n | tail -1)
[ -n "$newest" ] || { log "no checkpoints found; nothing to resume"; exit 0; }

max_steps=$(grep -E '^max_steps:' "$CONFIG" | awk '{print $2}')
[ -n "$max_steps" ] || { log "could not read max_steps from $CONFIG; refusing to guess"; exit 1; }

if [ "$newest" -ge "$max_steps" ]; then
    log "run already complete at step $newest/$max_steps; nothing to do"
    exit 0
fi

# The driver is not always ready the instant cron fires at boot.
sleep 30

log "resuming from step $newest/$max_steps (GPUS=$GPUS NPROC=$NPROC)"
nohup bash "$REPO/scripts/run_medium_training.sh" >> "$REPO/logs/supervisor.log" 2>&1 &
log "launched supervisor (pid $!)"
