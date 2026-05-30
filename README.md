# Reservation Notifier (Resy)

Python app intended to run on a **Jetson Nano** (ARM64 Linux). It periodically opens each venue’s **Resy booking page in headless Chrome** (via Selenium), reads visible reservation time labels (for example “7:00 PM”), filters by your configured date and time windows, and sends notifications when something matches.

## Important notes

- **Fragile but explicit**: Resy’s HTML can change without notice; the scraper looks for standalone `button` / `link` labels that match `7:00 PM` style times and ignores header/footer and the time-filter combobox. You may need to adjust the script in `checker.py` if the site layout shifts.
- **Browser stack required**: Chrome (or Chromium) plus a **matching chromedriver** for that version. Selenium’s built-in driver manager works on many desktops; on Jetson you usually install distro packages or pin known-good binaries and point the app at them (see below).
- Comply with Resy’s terms of service and **poll conservatively** (for example every 60–120 seconds).

## Requirements

- Python 3.8+ (Jetson often ships with 3.8; use `python3 --version`)
- Google Chrome or Chromium + compatible **chromedriver**
- **`python3-tk`** (Ubuntu/Debian Jetson images) only if you use **`--gui`** desktop mode:

```bash
sudo apt-get update && sudo apt-get install -y python3-tk
```

- Network access

## Setup on Jetson Nano

```bash
git clone https://github.com/M-Abram/reservation-notifier.git
cd reservation-notifier

# Use Python 3.8 explicitly (important if `python3` is still 3.6 on the system):
python3.8 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

Or let the launcher pick the newest Python (prefers `python3.8`, `python3.9`, … over plain `python3`):

```bash
chmod +x run-cli.sh
./run-cli.sh --check-deps
```

If you already created `.venv` with old Python 3.6, delete it first: `rm -rf .venv`, then run `./run-cli.sh` again.

**Do not run `pip install` outside a venv** — if you see `Defaulting to user installation because normal site-packages is not writeable`, activate the venv first (`source .venv/bin/activate`) or use `./run-cli.sh`.

You do **not** need `pip install -e .` if you use the launcher scripts below (they set `PYTHONPATH` for you). Optional install for a global `reservation-notifier` command:

```bash
pip install -e .
cp config.example.json config.json
# Edit config.json with your venues, dates, and times
```

**Before your first run on Linux**, check dependencies:

```bash
python -m reservation_notifier --check-deps
```

If Chrome or chromedriver is missing, install them (Debian/Ubuntu example):

```bash
sudo apt-get update
sudo apt-get install -y chromium chromium-driver
# Ubuntu 22.04 and older may use:
# sudo apt-get install -y chromium-browser chromium-chromedriver

export CHROME_BINARY=/usr/bin/chromium
export CHROMEDRIVER_PATH=/usr/bin/chromedriver
python -m reservation_notifier --check-deps
```

Typical environment variables (optional; use if Selenium cannot find the browser or driver):

```bash
export CHROME_BINARY=/usr/bin/chromium-browser   # or google-chrome path
export CHROMEDRIVER_PATH=/usr/bin/chromedriver
```

You can also set `checker.selenium_chrome_binary` and `checker.selenium_chromedriver_path` in `config.json`.

Run once:

```bash
python -m reservation_notifier
```

### Interactive mode (defaults to NYC)

If `config.json` is missing (or you pass `--interactive`), the app will prompt for:

- Restaurant name (searched in NYC)
- Seats / party size
- Date (format `YYYY-MM-DD`, e.g. `2026-05-30`)
- Time window start/end (24h `HH:MM`, e.g. `19:00`)

**Recommended on Linux** (avoids “no module named reservation_notifier”):

```bash
chmod +x run-cli.sh
./run-cli.sh --interactive
```

Or with venv activated:

```bash
source .venv/bin/activate
pip install -r requirements.txt
./run-cli.sh --check-deps
./run-cli.sh --interactive
```

Direct module invocation (requires `pip install -e .` **or** `export PYTHONPATH=$PWD/src`):

```bash
python -m reservation_notifier --interactive
```

### Desktop GUI mode (`--gui`)

Opens a Tk window where you fill in restaurant, seats, date, time window, and poll interval, then **Start search** / **Stop search**. Logs appear in the window (and still go to the terminal’s stderr).

Requires a display / desktop session (or X forwarding) and Tk (see **Requirements**).

```bash
export TK_SILENCE_DEPRECATION=1   # silences macOS “system Tk is deprecated” warning
python -m reservation_notifier --gui
```

Or use the launcher (sets that env var for you):

```bash
./run-gui.sh
```

On **macOS**, you can also double-click **`run-gui.command`** in Finder.

**If the window never appears** (common with Homebrew `python@3.9` in a venv): that build is often *not* a macOS “framework” Python, and Tk cannot show windows from the terminal. Fix by installing a framework Python and recreating the venv, for example:

```bash
brew install python-tk@3.12
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run-gui.sh
```

Or install Python from [python.org](https://www.python.org/downloads/) and recreate the venv with that interpreter.

Run continuously (example):

```bash
python -m reservation_notifier --loop
```

### Optional: systemd user service

Create `~/.config/systemd/user/reservation-notifier.service`:

```ini
[Unit]
Description=Resy reservation notifier
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/Documents/reservation-notifier
ExecStart=%h/Documents/reservation-notifier/.venv/bin/python -m reservation_notifier --loop --config %h/Documents/reservation-notifier/config.json
Restart=on-failure
RestartSec=30
Environment=NTFY_TOPIC=your-secret-topic
Environment=CHROME_BINARY=/usr/bin/chromium-browser
Environment=CHROMEDRIVER_PATH=/usr/bin/chromedriver

[Install]
WantedBy=default.target
```

Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now reservation-notifier.service
```

Adjust paths if your project lives elsewhere.

## Configuration

See `config.example.json`. You define:

- **venues**: each entry needs **`url_slug`** (path under `/venues/…`) and either **`city_url_slug`** (for example `new-york-ny`) or a **`location_shortcode`** we map (for example `ny` → `new-york-ny`). Optional numeric **`id`** is only used as an internal label in logs/slots.
- **`checker.party_size`**: becomes the `seats=` query parameter on the Resy URL.
- **`checker.selenium_*`**: headless mode, timeouts, extra wait after load (give client-side rendering time before scraping), optional explicit Chrome/chromedriver paths.
- **date_range**: start/end dates (inclusive) to search
- **time_windows**: preferred local time ranges (24h `HH:MM`, inclusive)
- **poll_interval_seconds**: delay between checks in loop mode
- **notifications**: console and optional webhook URL
- **ntfy**: environment variable **`NTFY_TOPIC`** for your [ntfy](https://ntfy.sh) topic; optional **`NTFY_SERVER`** (default `https://ntfy.sh`)

```bash
export NTFY_TOPIC="your-secret-topic"
python -m reservation_notifier --loop
```

See `config.example.json` as a starting point for automation / `--loop`.

## Project layout

```
reservation-notifier/
├── README.md
├── requirements.txt
├── config.example.json
└── src/reservation_notifier/
    ├── __init__.py
    ├── __main__.py
    ├── config.py
    ├── models.py
    ├── notifier.py
    ├── checker.py      # Selenium scrape + filters
    ├── tty_logging.py
    ├── gui_mode.py     # Tk desktop UI (optional `--gui`)
    ├── polling.py
    ├── _tk_env.py
    └── app.py
```
