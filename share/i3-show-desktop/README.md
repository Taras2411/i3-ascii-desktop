# Show and number i3 desktops

**Win+Ctrl+Space** (physical keycode 65, all layouts; triggers on release):

1. Compact/rename occupied workspaces into monitor columns: left 1/4/7/10, center 2/5/8, right 3/6/9. Preserve each monitor's current bar order. Only names change; applications, tiling containers and output placement stay intact.
2. Remove unused empty workspaces/placeholders. Reuse one empty workspace per output for the desktop view, or create the next free column number when none exists. No padding workspace is added to occupied columns. i3 always requires a visible workspace on each output.
3. Repeat the shortcut to restore the previous workspace views, focused container, fullscreen and sticky state. Keep the normalized numbers. Empty desktop workspaces disappear when hidden; apps opened there remain intact.

**Win+N** creates and focuses a new empty workspace on the currently focused output. It picks the first globally unused number in that monitor column, counting existing empty workspaces and numeric prefixes as occupied. It shares the toggle lock, keeps any desktop-view snapshot intact, and never renumbers existing workspaces. Static assignment mismatches after unplugging a monitor are corrected only for the newly created empty workspace.

**Win+number** uses native i3 workspace bindings. Output assignments in ~/.config/i3/config pin creation of numbers 1–10 to the correct monitors (Win+0 means 10). Sorting follows active outputs from left to right. Scratchpad windows are excluded. Existing windows are never moved between monitors by the script, including during restore.

Two-phase renaming handles swaps/collisions. Runtime state and lock: $XDG_RUNTIME_DIR/i3-show-desktop.*. State is written atomically before switching and belongs to the current i3 IPC session. Renamed workspaces resolve by ID; closed windows and disconnected outputs are handled gracefully. Versions 1 and 2 of saved state can be restored.

Commands: ~/.local/bin/i3-show-desktop, --new (create/focus), --status (on/off), --restore (return only, safe to repeat). No background daemon.

Unit checks: python3 -m unittest discover -s ~/.local/share/i3-show-desktop -p test_desktop.py -v.
