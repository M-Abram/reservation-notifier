from __future__ import annotations

import logging
import queue
import sys
import threading
import time as time_mod
from datetime import date, datetime, timedelta, time as timeofday
from typing import Callable, List

from reservation_notifier._tk_env import macos_framework_python_hint, prepare_tk_environment

prepare_tk_environment()

import tkinter as tk  # noqa: E402
from tkinter import messagebox, scrolledtext, ttk  # noqa: E402

from reservation_notifier.config import (
    AppConfig,
    CheckerSettings,
    DateRange,
    NotificationSettings,
    TimeWindow,
    Venue,
)
from reservation_notifier.checker import set_poll_abort_event, stop_active_browser
from reservation_notifier.notifier import notify
from reservation_notifier.polling import poll_filtered
from reservation_notifier.resy_search import ResyVenueHit, search_nyc_venues
from reservation_notifier.tty_logging import TtyColorFormatter

log = logging.getLogger(__name__)

GUI_LAYOUT_VERSION = "5"


def _configure_ttk_theme(root: tk.Tk) -> ttk.Style:
    """Use ``clam`` so widgets are visible on macOS (Aqua + dark mode often hides tk widgets)."""
    style = ttk.Style(root)
    for theme in ("clam", "alt", "default"):
        try:
            style.theme_use(theme)
            break
        except tk.TclError:
            continue
    bg = "#f2f2f2"
    style.configure(".", background=bg)
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground="#000000", font=("Helvetica", 12))
    style.configure(
        "TEntry",
        fieldbackground="#ffffff",
        foreground="#000000",
        insertcolor="#000000",
        font=("Helvetica", 12),
    )
    style.configure(
        "TCombobox",
        fieldbackground="#ffffff",
        background="#ffffff",
        foreground="#000000",
        arrowcolor="#000000",
        font=("Helvetica", 12),
    )
    style.configure("TButton", font=("Helvetica", 12), padding=6)
    style.configure("TLabelframe", background=bg)
    style.configure("TLabelframe.Label", background=bg, foreground="#000000", font=("Helvetica", 12, "bold"))
    style.configure("Header.TLabel", background=bg, foreground="#000000", font=("Helvetica", 18, "bold"))
    style.configure("Hint.TLabel", background=bg, foreground="#444444", font=("Helvetica", 11))
    try:
        root.configure(background=bg)
    except tk.TclError:
        pass
    return style


def _seat_choices() -> List[str]:
    return [str(n) for n in range(1, 21)]


def _date_choices(days: int = 120) -> List[str]:
    out: List[str] = []
    d = date.today()
    for i in range(days):
        cur = d + timedelta(days=i)
        out.append(f"{cur.isoformat()} ({cur.strftime('%a')})")
    return out


def _time_choices() -> List[str]:
    """24h HH:MM from 11:00 through 23:45 in 15-minute steps."""
    out: List[str] = []
    for hour in range(11, 24):
        for minute in (0, 15, 30, 45):
            out.append(f"{hour:02d}:{minute:02d}")
    return out


def _parse_date_choice(value: str) -> date:
    token = value.strip().split()[0]
    return date.fromisoformat(token)


def _configure_log_tags(widget: scrolledtext.ScrolledText) -> None:
    widget.tag_configure("log_error", foreground="#b00020")
    widget.tag_configure("log_warning", foreground="#a66300")
    widget.tag_configure("log_success", foreground="#0a7a2f")
    widget.tag_configure("log_poll", foreground="#005a9e")
    widget.tag_configure("log_muted", foreground="#666666")
    widget.tag_configure("log_info", foreground="#1a1a1a")
    widget.tag_configure("log_debug", foreground="#888888")


def _log_tag_for_record(record: logging.LogRecord, msg: str) -> str:
    text = msg.lower()
    if record.levelno >= logging.ERROR:
        return "log_error"
    if record.levelno >= logging.WARNING:
        return "log_warning"
    if "match found" in text or "reservation slots found" in text or "available:" in text:
        return "log_success"
    if "selenium poll:" in text:
        return "log_poll"
    if "no matching slots" in text:
        return "log_muted"
    if record.levelno <= logging.DEBUG:
        return "log_debug"
    return "log_info"


def _hit_to_venue(hit: ResyVenueHit) -> Venue:
    return Venue(
        name=hit.name,
        id=hit.venue_id,
        url_slug=hit.url_slug,
        location_shortcode=hit.location_shortcode,
        city_url_slug=hit.city_url_slug,
    )


class _GuiTextHandler(logging.Handler):
    def __init__(self, text_widget: scrolledtext.ScrolledText, root: tk.Tk) -> None:
        super().__init__()
        self._widget = text_widget
        self._root = root

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()

        def append() -> None:
            try:
                tag = _log_tag_for_record(record, msg)
                self._widget.configure(state="normal")
                self._widget.insert("end", msg + "\n", tag)
                self._widget.see("end")
                self._widget.configure(state="disabled")
            except Exception:
                pass

        try:
            self._root.after(0, append)
        except Exception:
            pass


def _setup_dual_logging(text_widget: scrolledtext.ScrolledText, root: tk.Tk) -> None:
    root_log = logging.getLogger()
    for h in root_log.handlers[:]:
        root_log.removeHandler(h)

    stderr_h = logging.StreamHandler(sys.stderr)
    tty_fmt = TtyColorFormatter(datefmt="%Y-%m-%dT%H:%M:%S%z")
    tty_fmt.bind_stream(stderr_h.stream)
    stderr_h.setFormatter(tty_fmt)
    root_log.addHandler(stderr_h)

    gui_h = _GuiTextHandler(text_widget, root)
    gui_h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    )
    root_log.addHandler(gui_h)
    root_log.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _pick_hit_modal(parent: tk.Tk, hits: list[ResyVenueHit]) -> ResyVenueHit | None:
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]

    top = tk.Toplevel(parent)
    top.title("Choose restaurant")
    top.transient(parent)
    top.grab_set()
    _configure_ttk_theme(top)

    ttk.Label(top, text="Several NYC venues matched. Pick one:").pack(
        fill="x", padx=8, pady=(8, 4)
    )

    lb = tk.Listbox(
        top,
        height=min(12, len(hits)),
        exportselection=False,
        width=72,
        bg="#ffffff",
        fg="#000000",
    )
    for h in hits:
        lb.insert("end", f"{h.name}  —  /{h.city_url_slug}/venues/{h.url_slug} (id {h.venue_id})")
    lb.pack(padx=8, fill="both", expand=True)

    picked: list[ResyVenueHit | None] = [None]

    def on_ok(_event=None) -> None:
        sel = lb.curselection()
        if not sel:
            messagebox.showwarning("Pick a row", "Select one restaurant.", parent=top)
            return
        picked[0] = hits[int(sel[0])]
        top.destroy()

    def on_cancel() -> None:
        top.destroy()

    lb.bind("<Double-Button-1>", on_ok)

    btnf = ttk.Frame(top)
    btnf.pack(pady=(4, 8))
    ttk.Button(btnf, text="Cancel", command=on_cancel).pack(side="right", padx=4)
    ttk.Button(btnf, text="OK", command=on_ok).pack(side="right", padx=4)

    top.protocol("WM_DELETE_WINDOW", on_cancel)
    lb.selection_set(0)
    lb.focus_set()
    parent.wait_window(top)
    return picked[0]


def _run_on_ui(root: tk.Tk, fn: Callable[[], None]) -> None:
    root.after(0, fn)


class ReservationGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Resy reservation notifier (NYC) — layout v{GUI_LAYOUT_VERSION}")
        self.root.minsize(680, 640)
        self.root.geometry("760x680")
        _configure_ttk_theme(root)

        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._cfg: AppConfig | None = None

        shell = ttk.Frame(root, padding=12)
        shell.pack(fill="both", expand=True)

        form = ttk.Frame(shell)
        form.pack(fill="x", anchor="n")
        form.columnconfigure(1, weight=1)

        r = 0
        ttk.Label(form, text="Resy Reservation Notifier", style="Header.TLabel").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        r += 1
        ttk.Label(
            form,
            text=f"Layout v{GUI_LAYOUT_VERSION} — if you only see buttons, quit and run: ./run-gui.sh",
            style="Hint.TLabel",
            wraplength=700,
        ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 10))
        r += 1

        def field_row(label: str, var: tk.StringVar, row_idx: int) -> tuple[int, tk.Entry]:
            ttk.Label(form, text=label).grid(row=row_idx, column=0, sticky="w", padx=(0, 8), pady=5)
            ent = ttk.Entry(form, textvariable=var, width=42)
            ent.grid(row=row_idx, column=1, sticky="ew", pady=5)
            return row_idx + 1, ent

        def combo_row(
            label: str,
            var: tk.StringVar,
            values: List[str],
            row_idx: int,
            *,
            default: str | None = None,
        ) -> tuple[int, ttk.Combobox]:
            ttk.Label(form, text=label).grid(row=row_idx, column=0, sticky="w", padx=(0, 8), pady=5)
            cb = ttk.Combobox(
                form,
                textvariable=var,
                values=values,
                state="readonly",
                width=40,
            )
            cb.grid(row=row_idx, column=1, sticky="ew", pady=5)
            if default is not None:
                var.set(default)
            elif values:
                var.set(values[0])
            return row_idx + 1, cb

        self.var_restaurant = tk.StringVar()
        r, self._ent_restaurant = field_row("Restaurant (NYC):", self.var_restaurant, r)

        seat_vals = _seat_choices()
        self.var_seats = tk.StringVar()
        r, self._cb_seats = combo_row("Seats:", self.var_seats, seat_vals, r, default="2")

        date_vals = _date_choices()
        today_label = date_vals[0] if date_vals else ""
        self.var_date = tk.StringVar()
        r, self._cb_date = combo_row("Date:", self.var_date, date_vals, r, default=today_label)

        time_vals = _time_choices()
        self.var_t0 = tk.StringVar()
        r, self._cb_t0 = combo_row("Time start:", self.var_t0, time_vals, r, default="19:00")

        self.var_t1 = tk.StringVar()
        r, self._cb_t1 = combo_row("Time end:", self.var_t1, time_vals, r, default="20:00")

        self.var_interval = tk.StringVar(value="30")
        r, self._ent_interval = field_row("Poll every (seconds):", self.var_interval, r)

        ttk.Label(
            form,
            text="Example: Lilia · 2026-05-30 · 19:00–20:00 · 2 seats · 24h times",
            style="Hint.TLabel",
            wraplength=700,
        ).grid(row=r, column=0, columnspan=2, sticky="w", pady=(4, 10))
        r += 1

        btn_row = ttk.Frame(form)
        btn_row.grid(row=r, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self.btn_start = ttk.Button(btn_row, text="Start search", command=self._on_start)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_stop = ttk.Button(btn_row, text="Stop search", command=self._on_stop, state="disabled")
        self.btn_stop.pack(side="left")
        self.var_status = tk.StringVar(value="Ready.")
        ttk.Label(btn_row, textvariable=self.var_status).pack(side="left", padx=(16, 0))

        log_section = ttk.Frame(shell)
        log_section.pack(fill="both", expand=True, pady=(8, 0))

        ttk.Label(log_section, text="Log").pack(anchor="w", fill="x", pady=(0, 2))

        legend = tk.Frame(log_section, bg="#f2f2f2")
        legend.pack(anchor="w", fill="x", pady=(0, 4))
        for text, _tag, color in (
            ("Info", "log_info", "#1a1a1a"),
            ("Poll", "log_poll", "#005a9e"),
            ("Match", "log_success", "#0a7a2f"),
            ("No slots", "log_muted", "#666666"),
            ("Warn", "log_warning", "#a66300"),
            ("Error", "log_error", "#b00020"),
        ):
            tk.Label(legend, text=text, fg=color, bg="#f2f2f2", font=("Helvetica", 10)).pack(
                side="left", padx=(0, 10)
            )

        self.txt = scrolledtext.ScrolledText(
            log_section,
            height=10,
            state="disabled",
            wrap="word",
            bg="#fafafa",
            fg="#1a1a1a",
            insertbackground="#000000",
            relief="solid",
            borderwidth=1,
        )
        self.txt.pack(fill="both", expand=True)
        _configure_log_tags(self.txt)

        _setup_dual_logging(self.txt, root)

        root.update_idletasks()
        log.info("GUI ready (layout v%s).", GUI_LAYOUT_VERSION)

    def _set_search_inputs_enabled(self, enabled: bool) -> None:
        entry_state = "normal" if enabled else "disabled"
        combo_state = "readonly" if enabled else "disabled"
        self._ent_restaurant.configure(state=entry_state)
        self._ent_interval.configure(state=entry_state)
        for cb in (self._cb_seats, self._cb_date, self._cb_t0, self._cb_t1):
            cb.configure(state=combo_state)

    def _parse_form(self) -> tuple[str, int, date, timeofday, timeofday, int]:
        name = self.var_restaurant.get().strip()
        if not name:
            raise ValueError("Restaurant name is required.")

        try:
            seats = int(self.var_seats.get().strip())
        except ValueError as e:
            raise ValueError("Seats must be a whole number.") from e
        if seats < 1 or seats > 20:
            raise ValueError("Seats must be between 1 and 20.")

        ds = self.var_date.get().strip()
        try:
            d = _parse_date_choice(ds)
        except ValueError as e:
            raise ValueError("Pick a date from the dropdown.") from e

        t0s = self.var_t0.get().strip()
        t1s = self.var_t1.get().strip()
        try:
            t0 = datetime.strptime(t0s, "%H:%M").time()
            t1 = datetime.strptime(t1s, "%H:%M").time()
        except ValueError as e:
            raise ValueError('Times must be HH:MM in 24h (e.g. "19:00").') from e

        try:
            interval = int(self.var_interval.get().strip())
        except ValueError as e:
            raise ValueError("Poll interval must be a whole number of seconds.") from e
        if interval < 5:
            raise ValueError("Poll interval must be at least 5 seconds.")

        return name, seats, d, t0, t1, interval

    def _on_start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            messagebox.showinfo("Already running", "A search is already in progress.", parent=self.root)
            return

        try:
            name, seats, day, t0, t1, interval = self._parse_form()
        except ValueError as e:
            messagebox.showerror("Invalid input", str(e), parent=self.root)
            return

        self.btn_start.configure(state="disabled")
        self.var_status.set("Looking up restaurant on Resy…")

        result_q: queue.Queue = queue.Queue()

        def lookup() -> None:
            try:
                hits = search_nyc_venues(name, per_page=8)
                result_q.put(("ok", hits))
            except Exception as e:
                result_q.put(("err", e))

        threading.Thread(target=lookup, daemon=True).start()
        self.root.after(100, lambda: self._finish_start_lookup(result_q, name, seats, day, t0, t1, interval))

    def _finish_start_lookup(
        self,
        result_q: queue.Queue,
        name: str,
        seats: int,
        day: date,
        t0: timeofday,
        t1: timeofday,
        interval: int,
    ) -> None:
        try:
            kind, payload = result_q.get_nowait()
        except queue.Empty:
            self.root.after(100, lambda: self._finish_start_lookup(result_q, name, seats, day, t0, t1, interval))
            return

        if kind == "err":
            self.btn_start.configure(state="normal")
            self.var_status.set("Ready.")
            messagebox.showerror("Search failed", f"Could not search Resy: {payload}", parent=self.root)
            return

        hits: list[ResyVenueHit] = payload
        if not hits:
            self.btn_start.configure(state="normal")
            self.var_status.set("Ready.")
            messagebox.showerror("No matches", f'No NYC venues matched "{name}".', parent=self.root)
            return

        chosen = _pick_hit_modal(self.root, hits)
        if chosen is None:
            self.btn_start.configure(state="normal")
            self.var_status.set("Ready.")
            return

        venue = _hit_to_venue(chosen)
        self._cfg = AppConfig(
            poll_interval_seconds=interval,
            venues=[venue],
            date_range=DateRange(start=day, end=day),
            time_windows=[TimeWindow(start=t0, end=t1)],
            notifications=NotificationSettings(console=True, webhook_url=None),
            checker=CheckerSettings(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                party_size=seats,
            ),
        )

        self._stop.clear()
        self._set_search_inputs_enabled(False)
        self.btn_stop.configure(state="normal")
        self.var_status.set("Searching… (click Stop to cancel)")
        log.info("Starting browser polling for %s…", venue.name)

        self._worker = threading.Thread(target=self._poll_loop, daemon=True)
        self._worker.start()

    def _on_stop(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            return
        if self._stop.is_set():
            return
        self._stop.set()
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Stopping…")
        # Re-enable fields immediately so the user can edit while Chrome shuts down.
        self._set_search_inputs_enabled(True)
        threading.Thread(target=self._stop_search_background, daemon=True).start()

    def _stop_search_background(self) -> None:
        stop_active_browser()
        log.info("Stop requested — closing browser.")

    def _poll_loop(self) -> None:
        assert self._cfg is not None
        cfg = self._cfg
        interval = max(5, cfg.poll_interval_seconds)
        set_poll_abort_event(self._stop)
        log.info(
            "Started search: %s | %s | seats=%s | window %s–%s | every %ss",
            cfg.venues[0].name,
            cfg.date_range.start.isoformat(),
            cfg.checker.party_size,
            cfg.time_windows[0].start.strftime("%H:%M"),
            cfg.time_windows[0].end.strftime("%H:%M"),
            interval,
        )

        try:
            while not self._stop.is_set():
                try:
                    slots = poll_filtered(cfg)
                except Exception:
                    if not self._stop.is_set():
                        log.exception("Poll iteration failed; retrying after wait")
                    slots = []

                if self._stop.is_set():
                    break

                if slots:
                    notify(cfg.notifications, slots)
                    log.info("Match found — stopping search.")
                    _run_on_ui(self.root, lambda: self.var_status.set("Match found — notified."))
                    break

                if self._stop.is_set():
                    break
                log.info("No matching slots this run.")
                deadline = time_mod.monotonic() + interval
                while time_mod.monotonic() < deadline:
                    if self._stop.wait(0.25):
                        log.info("Stopped by user.")
                        _run_on_ui(self.root, lambda: self.var_status.set("Stopped."))
                        return
        finally:
            set_poll_abort_event(None)
            self.root.after(0, self._on_worker_finished)

    def _on_worker_finished(self) -> None:
        self._worker = None
        self._stop.clear()
        set_poll_abort_event(None)
        self._set_search_inputs_enabled(True)
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.var_status.set("Ready.")


def _bring_window_forward(root: tk.Tk) -> None:
    root.update_idletasks()
    root.deiconify()
    root.lift()
    try:
        root.attributes("-topmost", True)
        root.after(200, lambda: root.attributes("-topmost", False))
    except tk.TclError:
        pass
    try:
        root.focus_force()
    except tk.TclError:
        pass


def run_gui_mode() -> None:
    hint = macos_framework_python_hint()
    if hint:
        print(hint, file=sys.stderr)

    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"Could not start Tk: {e}", file=sys.stderr)
        if hint:
            print(hint, file=sys.stderr)
        sys.exit(1)

    try:
        ReservationGui(root)
        _bring_window_forward(root)
        if hint:
            root.after(
                300,
                lambda: messagebox.showwarning(
                    "macOS Tk",
                    hint,
                    parent=root,
                ),
            )
        root.mainloop()
    except Exception as e:
        log.exception("GUI crashed")
        try:
            messagebox.showerror("GUI error", str(e))
        except Exception:
            print(f"GUI error: {e}", file=sys.stderr)
        raise
