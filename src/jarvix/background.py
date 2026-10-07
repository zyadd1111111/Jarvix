"""One owned background worker for timers, workflow triggers and task reminders."""
from __future__ import annotations

import threading

from jarvix.runtime import check_cancelled, operation


class BackgroundRuntime:
    def __init__(self, services, on_event=None, interval=2):
        self.s = services
        self.interval = max(.1, min(float(interval), 60))
        self._callback = on_event
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._tick_lock = threading.Lock()
        self._thread = None
        self._last_error = False
        self._suggestions = ()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def set_callback(self, callback):
        """UI bridges should emit Qt signals; this callback runs on the worker."""
        with self._lock:
            self._callback = callback

    def _emit(self, kind, data):
        with self._lock:
            callback = self._callback
        if callback:
            try:
                callback(kind, data)
            except Exception:
                pass  # Closing a UI view must not take down the scheduler.

    def start(self):
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="JarvixBackground", daemon=True)
            self._thread.start()
        return True

    def stop(self, timeout=5):
        self._stop.set()
        if hasattr(self.s, "workflows"):
            self.s.workflows.close()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(max(0, min(float(timeout), 30)))
        stopped = not self.running
        if not stopped:
            self._emit("background", {"status": "stopping", "error":
                "Waiting for the current background operation to stop; the worker is still active."})
        return stopped

    def _loop(self):
        self._emit("background", {"status": "running"})
        try:
            while not self._stop.is_set():
                try:
                    self.tick_once()
                    self._last_error = False
                except InterruptedError:
                    if self._stop.is_set():
                        break
                except Exception:
                    if not self._last_error:
                        self.s.repository.audit("error", "Background scheduler needs attention")
                        self._emit("background", {"status": "error", "error": "Background task failed; inspect activity."})
                    self._last_error = True
                self._stop.wait(self.interval)
        finally:
            self._emit("background", {"status": "stopped"})

    def tick_once(self):
        if self._stop.is_set() or not self._tick_lock.acquire(blocking=False):
            return {"workflows": [], "reminders": []}
        try:
            with operation(cancel=self._stop, timeout=120, max_steps=64, unattended=True):
                check_cancelled()
                if hasattr(self.s, "scheduler"):
                    self.s.scheduler.drain(cancel=self._stop)
                check_cancelled()
                legacy = self.s.run_due_automations()
                check_cancelled()
                workflows = self.s.workflows.tick(cancel=self._stop, on_event=self._emit) if hasattr(self.s, "workflows") else []
                check_cancelled()
                reminders = self.s.due_reminders()
                for reminder in reminders:
                    check_cancelled()
                    # Local notifications contain only the task title, never private task bodies.
                    self.s.notifications.create("Task reminder", reminder["title"], "task", "normal")
                    self._emit("reminder", {"id": reminder["id"], "title": reminder["title"]})
                check_cancelled()
                # Delivery applies quiet/DND preferences and bounds each batch.
                for notification in self.s.notifications.delivery():
                    self._emit("notification", notification)
                if legacy:
                    self._emit("automations", {"completed": legacy})
                if hasattr(self.s, "proactive"):
                    suggestions = self.s.proactive.refresh()
                    identities = tuple(item["id"] for item in suggestions)
                    if identities != self._suggestions:
                        self._suggestions = identities
                        self._emit("suggestions", {"count": len(identities)})
                return {"workflows": workflows, "reminders": reminders}
        finally:
            self._tick_lock.release()
