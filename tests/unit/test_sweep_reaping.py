"""The sweep driver does not leave training runs on the cards when it exits.

`paper/NIGHT-PLAN.md` records the failure this covers, and recorded it as still
open for several days: a sweep started before the previous one had released its
memory, OOMed, and tripped its own circuit breaker -- but two runs that had
already started kept training as orphans, holding 8.6 GB each, after the driver
had exited. A 124M fine-tune needs ~8.6 GB of an 11.26 GB card, so an orphan is
enough to lose the next grid.

Worker threads are daemons, which is what made this possible: the interpreter
exits without unwinding them, so nothing on the way out had ever killed the
`finetune.py` child a worker was blocked on. These tests pin the reaper that
now does, using `sleep` subprocesses in place of training runs.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import sweep_lr_packing as sweep  # noqa: E402


@pytest.fixture(autouse=True)
def clean_registry():
    """Leave the module-level registry as it was found.

    It is process-global, so a test that abandoned an entry would make the next
    one reap a child it never started.
    """
    sweep._children.clear()
    sweep._stopping.clear()
    yield
    for child in list(sweep._children):
        child.kill()
    sweep._children.clear()
    sweep._stopping.clear()


def spawn(source: str) -> subprocess.Popen:
    child = subprocess.Popen([sys.executable, "-c", source],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sweep._children.add(child)
    return child


def test_reaps_a_live_child():
    child = spawn("import time; time.sleep(120)")
    assert child.poll() is None, "child should still be running before the reap"

    sweep._reap_children()

    assert child.poll() is not None, "reaper left a training run on the card"


def test_kills_a_child_that_ignores_sigterm():
    """SIGTERM is a request. A wedged run gets SIGKILL rather than the card."""
    child = spawn(
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('ready', flush=True)\n"
        "time.sleep(120)\n"
    )
    time.sleep(1.0)  # let the handler be installed before we signal it

    started = time.monotonic()
    sweep._reap_children(grace=2.0)
    elapsed = time.monotonic() - started

    assert child.poll() is not None, "a run that ignored SIGTERM survived the reap"
    assert elapsed < 30, f"reaper waited {elapsed:.1f}s on a run that ignores SIGTERM"


def test_reaping_is_a_no_op_with_nothing_running():
    """The normal exit path: every run already finished and deregistered."""
    child = spawn("pass")
    child.wait()

    sweep._reap_children()  # must not raise on an already-dead child


def test_signal_stops_workers_and_exits_with_the_signal():
    assert not sweep._stopping.is_set()

    with pytest.raises(SystemExit) as excinfo:
        sweep._on_signal(15, None)

    assert sweep._stopping.is_set(), "workers would keep pulling work off the queue"
    assert excinfo.value.code == 143, "exit status should record the signal"
