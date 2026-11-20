"""Run blocking work off the UI thread and deliver the result back on it.

    run_bg(lambda: backend.findframe.analyze(ref), on_done=lambda result: ..., on_error=lambda exc: ...)

Also accepts concurrent.futures.Future objects (e.g. from ObsManager):
    when_done(backend.obs.devices(), lambda devices: ...)
"""
import threading

from PySide6.QtCore import QObject, Signal, Slot


class _Bridge(QObject):
    call = Signal(object, object)

    def __init__(self):
        super().__init__()
        # bound slot of an object living on the UI thread → queued delivery
        self.call.connect(self._invoke)

    @Slot(object, object)
    def _invoke(self, fn, arg):
        fn(arg)


_bridge = None


def _get():
    """Must first be called on the UI thread (done at app start-up)."""
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()
    return _bridge


def run_bg(fn, on_done=None, on_error=None):
    b = _get()

    def work():
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            if on_error:
                b.call.emit(on_error, e)
            else:
                print("background task failed:", e)
            return
        if on_done:
            b.call.emit(on_done, res)
    threading.Thread(target=work, daemon=True).start()


def when_done(future, on_done, on_error=None):
    b = _get()

    def cb(f):
        try:
            res = f.result()
        except Exception as e:  # noqa: BLE001
            if on_error:
                b.call.emit(on_error, e)
            return
        b.call.emit(on_done, res)
    future.add_done_callback(cb)


def on_main(fn, arg=None):
    """Schedule fn(arg) on the UI thread from any thread."""
    _get().call.emit(fn, arg)
