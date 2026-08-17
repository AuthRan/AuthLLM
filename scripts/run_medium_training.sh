#!/usr/bin/env bash
# Supervised launcher for the multi-day `medium` (124M) run on 2x RTX 2080 Ti.
#
# Two jobs:
#   1. Wait for scripts/prepare_data.py to finish, and refuse to start if it
#      did not produce a usable manifest. Training against a half-written
#      corpus would silently train on whatever happened to land on disk.
#   2. Supervise training. A multi-day run has to survive a transient CUDA
#      fault or an OOM blip, so each attempt resumes from the newest
#      checkpoint rather than restarting from step 0.
#
# Usage:  nohup bash scripts/run_medium_training.sh > logs/supervisor.log 2>&1 &

set -uo pipefail
cd "$(dirname "$0")/.."

PYTHON=.venv/bin/python
TORCHRUN=.venv/bin/torchrun

# Which GPUs to train on, and how many processes to launch.
#
# Defaults to both cards. Override to dodge a thermally-limited GPU:
#     GPUS=0 NPROC=1 nohup bash scripts/run_medium_training.sh ...
#
# On this machine GPU 1 has almost no airflow: it reaches its 89C max
# operating temperature and drops to 300 MHz (vs GPU 0's 1800 MHz) while
# drawing only ~120W, so it is thermally limited rather than power limited and
# a power cap does not help it. Because DDP is synchronous, GPU 0 then waits on
# GPU 1 at every all-reduce and the whole run proceeds at the throttled card's
# pace -- measured ~11.7k tok/s for both cards versus ~32k tok/s for GPU 0
# alone. Until the airflow is fixed, one healthy card beats two mismatched ones.
GPUS=${GPUS:-0,1}
NPROC=${NPROC:-2}
export CUDA_VISIBLE_DEVICES="$GPUS"
DATA_DIR=data/fineweb_edu_5B
MANIFEST=$DATA_DIR/manifest.json
CKPT_DIR=checkpoints/medium
LOG_DIR=logs
MAX_ATTEMPTS=20

mkdir -p "$CKPT_DIR" "$LOG_DIR"

# Refuse to start if training is already running. Two concurrent runs fit in
# VRAM and both make progress, so nothing crashes -- they just time-slice the
# same two GPUs and each reports roughly a third of the expected throughput,
# which reads as a mysterious performance bug rather than as duplicate work.
# (This is not hypothetical: it happened, and cost an hour of misdiagnosis.)
EXISTING=$(pgrep -fc "scripts/train\.py" || true)
if [ "${EXISTING:-0}" -gt 0 ]; then
    echo "[supervisor] FATAL: $EXISTING scripts/train.py process(es) already running. Refusing to start a second run."
    echo "[supervisor] Stop them first:  ps -eo pid,cmd | grep '[s]cripts/train.py' | awk '{print \$1}' | xargs -r kill -9"
    exit 1
fi

echo "[supervisor] $(date -Is) waiting for data preparation to finish..."
while pgrep -f "prepare_data.py" > /dev/null; do
    sleep 20
done

if [ ! -f "$MANIFEST" ]; then
    echo "[supervisor] FATAL: $MANIFEST missing -- data prep did not complete. Not starting training."
    exit 1
fi

TOTAL_TOKENS=$("$PYTHON" -c "import json;print(json.load(open('$MANIFEST'))['total_tokens'])")
echo "[supervisor] $(date -Is) data ready: $TOTAL_TOKENS tokens"

# A corpus far short of the target means the download died early; training
# would then quietly loop over a fraction of the data for days.
if [ "$TOTAL_TOKENS" -lt 1000000000 ]; then
    echo "[supervisor] FATAL: only $TOTAL_TOKENS tokens (<1B). Refusing to launch a multi-day run on this."
    exit 1
fi

# Run the GPU memory/throughput sweep before training claims both cards for
# days. It needs a quiet machine to produce trustworthy per-step timings, and
# this is the only quiet window there will be -- data prep has just exited and
# training has not started.
BENCH_LOG="$LOG_DIR/benchmark_cuda.log"
if [ ! -f "$BENCH_LOG" ]; then
    echo "[supervisor] $(date -Is) running CUDA memory benchmark (quiet machine) -> $BENCH_LOG"
    # Hard timeout: a hung benchmark must never block the run it precedes.
    timeout 1800 "$PYTHON" scripts/benchmark_memory.py --device cuda > "$BENCH_LOG" 2>&1
    echo "[supervisor] $(date -Is) benchmark finished (status $?)"
fi

for attempt in $(seq 1 $MAX_ATTEMPTS); do
    # Newest checkpoint by step number, if any. Sorting numerically on the
    # step field matters -- lexicographic order puts step_9000 after step_10000.
    LATEST=$(ls -1 "$CKPT_DIR"/step_*.pt 2>/dev/null | sed 's/.*step_//; s/\.pt//' | sort -n | tail -1)
    if [ -n "$LATEST" ]; then
        RESUME=(--resume-from "$CKPT_DIR/step_$LATEST.pt")
        echo "[supervisor] $(date -Is) attempt $attempt: resuming from step $LATEST"
    else
        RESUME=()
        echo "[supervisor] $(date -Is) attempt $attempt: starting from scratch"
    fi

    echo "[supervisor] launching on CUDA_VISIBLE_DEVICES=$GPUS with nproc_per_node=$NPROC"
    "$TORCHRUN" --nproc_per_node="$NPROC" scripts/train.py \
        --model configs/model/medium.yaml \
        --train configs/train/fineweb_2x2080ti.yaml \
        --tokenizer tokenizer_gpt2.json \
        --data-manifest "$MANIFEST" \
        --checkpoint-dir "$CKPT_DIR" \
        --log-path "$LOG_DIR/medium_metrics.csv" \
        "${RESUME[@]}"
    STATUS=$?

    if [ $STATUS -eq 0 ]; then
        echo "[supervisor] $(date -Is) training finished successfully"
        exit 0
    fi

    echo "[supervisor] $(date -Is) attempt $attempt exited with status $STATUS; retrying in 60s"
    sleep 60
done

echo "[supervisor] giving up after $MAX_ATTEMPTS attempts"
exit 1
