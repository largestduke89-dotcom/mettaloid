"""Dev-only UI smoke test: visit routes, save window screenshots, log exceptions.

  MT_SNAPSHOT_DIR=out  MT_SNAPSHOT_ROUTES="home,library,clip:{first}"  MT_SNAPSHOT_PRE="python code"
"""
import os
import sys
import traceback

from PySide6.QtCore import QTimer


def run_snapshots(app, out):
    os.makedirs(out, exist_ok=True)
    errors = []
    old_hook = sys.excepthook

    def hook(t, v, tb):
        errors.append("".join(traceback.format_exception(t, v, tb)))
        old_hook(t, v, tb)
    sys.excepthook = hook
    app.show_window()
    win = app.window
    pre = os.environ.get("MT_SNAPSHOT_PRE")
    routes = os.environ.get("MT_SNAPSHOT_ROUTES", "home,library,compressor,findframe,games,settings").split(",")
    first = app.backend.library.list()[0]["id"] if app.backend.library.list() else ""
    steps = []
    if pre:
        steps.append(("pre", pre))
    for r in routes:
        steps.append(("route", r.replace("{first}", first)))
    wait = int(os.environ.get("MT_SNAPSHOT_WAIT", "1800"))

    def step(i=0):
        if i >= len(steps):
            with open(os.path.join(out, "errors.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(errors))
            app.quit()
            return
        kind, val = steps[i]
        try:
            if kind == "pre":
                exec(val, {"app": app, "win": app.window, "backend": app.backend})  # noqa: S102
            else:
                route, _, q = val.partition("?")
                params = dict(x.split("=", 1) for x in q.split("&") if "=" in x)
                if ":" in route:
                    route, _, rid = route.partition(":")
                    params["id"] = rid
                if route != "stay":
                    app.window.go(route, **params)
        except Exception:  # noqa: BLE001
            errors.append(traceback.format_exc())

        def shot():
            if kind == "route":
                name = val.replace(":", "_").replace("?", "_").replace("=", "_").replace("&", "_")[:60]
                app.window.grab().save(os.path.join(out, name + ".png"))
            step(i + 1)
        QTimer.singleShot(wait if kind == "route" else int(os.environ.get("MT_SNAPSHOT_PRE_WAIT", "3000")), shot)
    step()
    _ = win
