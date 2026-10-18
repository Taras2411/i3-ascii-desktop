# Central ASCII desktop

Start: `~/.local/bin/center-desktop`. Starts automatically with i3 and refuses duplicate instances. The middle active output (ordered by X position) is the target. Existing weather and system surfaces are independent.

- Arch: left-drag rotates a virtual trackball; drag around the edge for roll. Release adds damped inertia, followed by a smooth return to automatic rotation. Wheel changes auto speed from 0.1× to 8× (default 40 seconds/turn), with easing; the speed is saved. Right-click pauses/resumes. Renders at 20 fps while visible; pauses beneath a fully covered desktop.
- Tasks: [+] adds, checkbox completes, text edits, wheel scrolls. Data is local.
- Focus: 25/5 minutes, manual transitions, desktop notification. The saved absolute deadline survives restarts.
- Projects: up to three valid repositories under ~/1work, depth four; recent commit/HEAD activity and changed files determine order. Click opens a terminal there.
- Music: MPRIS/playerctl, source selector, ASCII cover, playback controls and seeking if supported. The refresh step is adjusted by wheel over the footer (0.5–10 seconds). Paused/stopped sources can retain their current title; missing covers use a placeholder.
- Calendar: [Connect Google] imports a Desktop OAuth client and authorizes in the browser. Instructions: GOOGLE-SETUP.html. Select calendars in Settings. Click a day or event, [+] creates an event. Event times are Prague local; all-day end dates in the form are inclusive. Calendar step: 1–30 min. Changing a recurring instance affects that occurrence only. Series editing opens Google's UI. Google Tasks is not used.

## Private state

This directory is mode 0700. state.json, google-client.json, google-token.json, calendar-cache.json and pending-event.json use mode 0600 with atomic replacement. Do not commit or share those files. Cover cache is bounded to 32 entries. No Google credentials are bundled. OAuth refresh token revocation requires reconnection; an External app in Testing can require login after seven days (see setup guide).

If a create response is lost, retry the same unchanged form. An event ID and original payload are persisted before insertion. After restarting, use Settings → Check and finish previous write. This resolves the same identifier before allowing another insertion. Edit requests use If-Match to detect concurrent modifications. No recurring-series creation, attendee editing or Google Meet creation is provided.

## Maintenance

Python packages are isolated in venv; versions are in requirements.lock. Logs: center.log. Check i3 config: `i3 -C -c ~/.config/i3/config`.

Tests: `~/.local/share/center-desktop/venv/bin/python -m unittest discover -s ~/.local/share/center-desktop -p test_center.py -v`.

The live Google read/write acceptance test requires the owner's OAuth JSON and browser authorization. After connection create a clearly named temporary event, edit it, reload the panel, and delete it; verify changes in Google Calendar. Tests use simulated API responses and do not access an account.

Language: click the gray gear at the bottom-left of the left monitor, or run `desktop-language en|mixed`. The shared setting updates all widgets live. With a linked installation, code comes directly from the repository; private state stays in this data directory. Use `desktop-restart` after Python source changes.
