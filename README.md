# i3 ASCII Desktop

Three interactive desktop surfaces for a three-monitor i3/X11 setup. They sit beneath application windows and remain visible on empty workspaces, without occupying tiling containers or taking keyboard focus. Choose a fully English interface or the original mixed Russian/English interface. Source comments and documentation are in English; translations are isolated in a locale catalog.

```text
 LEFT / SYSTEM                 CENTER / PERSONAL                 RIGHT / WEATHER
 +----------------------+      +-----------------------------+   +-------------------+
 | CPU + GPU fan bars   |      | Calendar   Clock   Tasks    |   | Prague   HH:MM:SS |
 | CPU / temp overlays  |      |                             |   | Current weather   |
 | RAM   Network   GPU  |      | Projects   3D Arch   Focus  |   | Clothing advice   |
 | Processes   Disks   |      |                             |   | 12h temperature   |
 |          (2x2 rings) |      |      Large music player     |   | 12h precipitation |
 +----------------------+      +-----------------------------+   | 3-day forecast    |
                                                                 +-------------------+
```

## Features

- **System desktop:** CPU, per-thread graphs on click, NVIDIA GPU and VRAM, RAM, network upload/download, process lists and a 2×2 disk ring grid. Temperature curves overlay load curves in another color. Charts use real timestamps and smooth interpolation. Disk labels use mount basenames and GB callouts.
- **Fan controls:** large CPU/GPU speed bars, click/drag manual duty, AUTO/FULL/SILENT presets. A separate optional root service enforces limits, thermal overrides and a heartbeat lease. The supplied CPU controller targets specific reference hardware; see [fan controls](services/fan-control/README.md).
- **Weather:** Prague conditions, ASCII weather art, 12-hour temperature and precipitation probability graphs, three-day forecast and sunrise/sunset. Clothing recommendations use apparent temperature, rain/snow, wind and evening cooling. Weather data: [Open-Meteo](https://open-meteo.com/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The default API endpoint is for non-commercial use; see its [terms and plans](https://open-meteo.com/en/pricing).
- **Central desktop:** Google Calendar with event creation/editing/deletion, three priority tasks, a persisted 25/5 focus timer, recent Git projects and a large MPRIS music player. Color ASCII covers preserve the entire image and its physical aspect ratio.
- **Clocks:** all three widgets show seconds. The central clock offers Nixie, animated split-flap and Apollo DSKY styles. Apollo digits have slanted separated segments and supersampled edges.
- **Interactive Arch logo:** drag a virtual trackball to rotate all axes, release for inertia and a smooth return to automatic rotation, scroll to adjust speed, right-click to pause. Rendering pauses when covered.
- **Desktop shortcuts:** show every monitor's wallpaper and restore previous views; compact workspace numbering without moving application windows; create a new numbered workspace on the active monitor.
- **Desktop integration:** stable Canvas item reuse to reduce flicker; adjustable refresh **step** using the mouse wheel; two-hour idle blanking/DPMS; optional physical-key EN/RU/UA layout shortcuts.

## Requirements

Linux, i3 on X11, Python 3.11 or newer with Tk, X11/Xext libraries and a monospaced font. The tested setup is Arch Linux with Python 3.14 and Noto Sans Mono. Wayland compositors are not supported. Output selection follows active monitors' X positions: leftmost for system, middle for center, rightmost for weather. Three monitors are the intended layout; with fewer, surfaces can overlap.

For Arch Linux, the core packages are:

```sh
sudo pacman -S --needed i3-wm python tk python-pip libx11 libxext \
  noto-fonts playerctl glib2 xdg-utils libnotify xorg-xrandr pciutils
```

The central app installs its Python dependencies into its own virtual environment using the included [requirements lock](share/center-desktop/requirements.lock). System/weather use the standard library. Music controls need `playerctl`; capability queries use `gdbus` from GLib. NVIDIA telemetry requires the proprietary driver's NVML library. CPU sensors depend on available Linux hwmon drivers. Missing optional telemetry is displayed as unavailable.

The full example i3 config also references optional desktop utilities: `i3status-rust`, optional `speedread` and `snixembed`, `rofi`, `i3lock`, `xss-lock`, `dex`, `network-manager-applet`, `copyq`, `pasystray`, `pavucontrol`, `libpulse`, `maim`, `xclip`, `xorg-setxkbmap` and a terminal. Install those you use or remove their example lines.

## Install

```sh
git clone https://github.com/Taras2411/i3-ascii-desktop.git
cd i3-ascii-desktop
python3 install.py --link
```

With `--link`, launchers and sources point directly to this checkout: future edits have one Git source. Keep the checkout at a stable path. The Python environment lives in `.venv` inside the checkout. Without `--link`, the installer copies launchers into `~/.local/bin`, app sources into `~/.local/share`, and config examples into `~/.local/share/i3-ascii-desktop/config`. Replaced source/config-example files are backed up under `~/.local/share/i3-ascii-desktop/backups/`. Existing personal app state is preserved. The installer does not modify the active i3 config, start widgets, or install a root service.

Merge [config/i3-widgets.conf](config/i3-widgets.conf) into your i3 config, or add:

```i3
include ~/.local/share/i3-ascii-desktop/config/i3-widgets.conf
```

This snippet assumes `$mod` is already defined. Remove duplicate startup commands and key bindings. For a complete starting point, see [i3.conf](config/i3.conf) and [i3status-rust.toml](config/i3status-rust.toml). Replace the example `$left`, `$center`, `$right` output names with those from `xrandr --query`; adapt the workspace output assignments too. The [monitor layout script](config/monitors.sh) targets the reference three-monitor arrangement. The full config enables it; adapt it before linking configs on another machine. Installing app links alone does not apply it.

Validate your merged config with `i3 -C -c ~/.config/i3/config`, then reload i3. The widgets refuse duplicate instances. After changing source, run `~/.local/bin/desktop-restart` to load the new code. Personal state is retained.

Each widget also starts directly:

```sh
~/.local/bin/system-desktop
~/.local/bin/center-desktop
~/.local/bin/prague-weather
```

`python3 install.py --home /tmp/desktop-staging --skip-deps` stages source/config files for inspection without creating a Python environment. A staged central launcher needs its dependencies installed before use.

## Language

Click **[ EN / MIX ]** in the central widget header to select **English** or **Mixed (current style)**. All three widgets update without a restart. The choice is saved under `~/.config/i3-ascii-desktop/settings.json`, outside Git. Calendar events, tasks, track metadata and other user content retain their original language.

```sh
~/.local/bin/desktop-language en
~/.local/bin/desktop-language mixed
~/.local/bin/desktop-language --get
~/.local/bin/desktop-language  # Open the standalone picker
```

New installations default to English. Language changes apply to the panels and newly opened dialogs; close and reopen an existing dialog to refresh its labels. `--language mixed` selects the original appearance during installation. `DESKTOP_LANGUAGE=en` or `mixed` temporarily overrides the saved setting for a process. The Google setup guide has both language variants.

## One checkout for code and active configs

On the reference setup, the following command links application source, the active i3 config, the bar config and the monitor script to this checkout, backing up replaced files first:

```sh
python3 install.py --link --link-config --language mixed
i3 -C -c ~/.config/i3/config
i3-msg reload
~/.local/bin/desktop-restart
```

Review output names, modes and optional utilities in `config/i3.conf` and `config/monitors.sh` before applying the full reference config on another machine. In link mode, both `~/.local/bin` commands and the legacy app source paths resolve to repository files. Personal JSON, OAuth files, covers, logs and caches remain in their existing private locations. `git pull --ff-only` updates this checkout; restart the widgets to load Python changes. Reload i3 after config changes. Re-run the link installer if a change adds new source files or launchers.

The root fan daemon remains a root-owned deployed copy, since a root service must not execute editable user code via a symlink. Its source of truth is still `services/fan-control/daemon.py` in this repository. After changes to that service, run `sudo python3 scripts/install-fan-service.py --user "$USER" --restart` to deploy and restart it. The deployed Python file should match the repository source byte for byte; the service template substitutes only the authorized user's numeric UID.

## Controls

| Interaction | Result |
| --- | --- |
| Win+Ctrl+Space | Show wallpaper on all outputs; repeat to restore workspace views/focus |
| Win+N | Create and focus the first free workspace number for the active output |
| Win+1…9, Win+0 | Native workspaces 1…9, 10 in the full example config |
| Wheel over a `step` label | Change that widget/data source's refresh interval |
| Click CPU graph | Toggle total CPU and per-thread charts |
| Click/drag fan bar | Request manual fan duty, if the optional service is installed |
| Click central clock | Choose Nixie, split-flap or Apollo |
| Drag / wheel / right-click Arch | Rotate / change auto speed / pause |
| Calendar day / event / [+] | Select date / inspect-edit event / create event |
| Task checkbox / text / [+] | Complete / edit / add a local task |

Workspace columns follow physical output order: left **1,4,7,10…**, center **2,5,8…**, right **3,6,9…**. Showing the desktops compacts occupied workspace numbers and removes excess empty workspaces; one visible workspace per output is still required by i3. Only workspace names change: application placement and tiling layouts are preserved. Restoring keeps the new numbers. See [workspace details](share/i3-show-desktop/README.md).

Optional keyboard bindings use physical keys: Win+Space cycles English/Russian; Win+U toggles Ukrainian and returns to the previous layout. They are commented out in the minimal snippet and enabled in the full example.

## Google Calendar

Follow the [setup guide](share/center-desktop/GOOGLE-SETUP.html), also available from the connection dialog. You provide your own Desktop app OAuth client. Google Calendar is optional; music, clocks, projects, focus and tasks work without it. Calendar times use `Europe/Prague`.

Event writes support conflict detection and recovery after uncertain network responses. Recurring edits apply to a single occurrence; the full series opens in Google's web UI. Attendee/Meet editing and recurring-series creation are not implemented. See [central app details](share/center-desktop/README.md).

## Optional fan service

The reference CPU implementation is for an **ASRock H310M-HDV/M.2**, **nct6791**, **fan2/pwm2**, firmware AUTO value **5**. GPU controls use NVIDIA NVML at PCI address **0000:01:00.0**. The systemd sysfs write allowance is also machine-specific. This is not a generic fan controller; port the hardware mapping and service permissions together for a different machine. Dashboard monitoring works without this service.

On the supported hardware, install the service files for your desktop account:

```sh
sudo python3 scripts/install-fan-service.py --user "$USER"
sudo systemctl daemon-reload
sudo modprobe nct6775
sudo systemctl enable --now desktop-fan-control.service
```

The service installer itself does not start the service. It backs up existing files under `/var/backups/i3-ascii-desktop/`. The service authenticates UNIX peers using the installed numeric UID, accepts only fixed fan operations, and runs separately from user widget code. A 15-second heartbeat expiry restores AUTO. CPU/GPU thermal overrides engage at 85/80°C with 5°C hysteresis. Missing temperature data restores AUTO. Restart/stop also restores AUTO. More details: [fan service](services/fan-control/README.md).

## Settings and private state

| Location | Contents |
| --- | --- |
| `~/.local/share/center-desktop/` | Central source, venv, local tasks/settings, OAuth tokens, calendar cache and log |
| `~/.cache/prague-weather/` | Forecast cache and refresh step |
| `~/.cache/system-desktop/` | Refresh step and CPU graph preference |
| `$XDG_RUNTIME_DIR/i3-show-desktop.*` | Session-bound desktop snapshot and lock |
| `/run/desktop-fans/control.sock` | Optional fan service socket |

Central personal JSON files use mode 0600; its data directory uses 0700. No credentials, event caches, personal tasks, album-art caches or screenshots of personal data are included in this repository. Runtime files are excluded by `.gitignore`. Do not use the installed app directory as your Git checkout.

Projects are discovered under `~/1work` by default. Override this with `DESKTOP_PROJECTS_DIR=/path/to/projects` in the central app's environment. Weather coordinates and timezone are constants at the top of `bin/prague-weather`; central calendar timezone is in `share/center-desktop/core.py`.

## Checks

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r share/center-desktop/requirements.lock
.venv/bin/python scripts/check.py
```

The checks exercise clocks, 3D motion, cover geometry, focus persistence, calendar dates/write recovery, workspace numbering/restore, fan safety and staged installation. API/hardware interactions in unit tests use fakes. CI also validates the reference i3 syntax and language/link installation behavior. Live Google write acceptance requires your own account; no credentials are needed for unit tests.

For local telemetry inspection: `python3 bin/system-desktop --sample`. For a headless weather update: `python3 bin/prague-weather --fetch` (this writes your local forecast cache and contacts Open-Meteo).

## Remove or restore

Remove the added i3 lines, stop the three widget processes, and delete the installed launchers if no longer needed. Restore earlier source files from the installer backup if replacing an older installation. Keep central personal state unless you intentionally want to erase it. Google access can be revoked in your Google account's connected-app settings.

If installed, stop/disable `desktop-fan-control.service` before removing its service file, `/usr/local/lib/desktop-fan-control.py` and optional modules-load config, then run `systemctl daemon-reload`. Stopping the service restores AUTO; restore an earlier backed-up service if needed.
