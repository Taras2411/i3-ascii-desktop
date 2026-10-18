# Desktop fan controls

The system desktop talks to `/run/desktop-fans/control.sock`. The root service
accepts only the user selected during installation via Linux peer credentials, and only fixed fan operations.
It never imports the user's dashboard code or executes client-provided commands.

Optional installed components:

- `/usr/local/lib/desktop-fan-control.py`: root-owned controller, Python isolated mode.
- `/etc/systemd/system/desktop-fan-control.service`: enabled system service.
- `/etc/modules-load.d/desktop-sensors.conf`: Nuvoton sensor driver at boot.

## Controls

- AUTO: firmware Smart Fan IV for CPU; NVIDIA's default fan policy for GPU.
- FULL: request 100% for the CPU and all fans reported by NVIDIA.
- SILENT: the controller follows the temperature curves defined in `CURVES`.
  Below 45°C GPU fan-stop decisions are delegated to NVIDIA's automatic policy.
- Clicking or dragging a bar selects MANUAL. CPU minimum is 35% PWM; GPU minimum
  is 30%. RPM is measured separately and is not the slider's unit.

## Recovery

The dashboard renews a 15-second lease every two seconds. If it stops renewing
the lease, the controller restores both devices to AUTO. Selected non-auto modes
are not persisted across service restarts or reboot.

At 85°C CPU / 80°C GPU, non-auto modes are overridden to FULL. Override remains
until temperature falls at least 5°C. A missing/invalid temperature reading
restores AUTO. A service stop restores AUTO; systemd also invokes a separate
restore process after service failure and restarts the daemon. The systemd
watchdog detects an unresponsive controller.

CPU control is specifically mapped to the ASRock H310M-HDV/M.2, NCT6791 fan2.
Firmware curves, sensor assignments, PWM/DC mode and other fan channels are
preserved. Writes to PWM duty require manual mode on this chip; transition
starts with full-speed mode, then sets the requested manual duty.

## Validation

Run `python3 test_controller.py` for validation of bounds, modes, overheating,
hysteresis, telemetry failure, peer authorization and heartbeat expiry.
Hardware checks should always end in AUTO. Restore manually with:

    sudo systemctl restart desktop-fan-control.service

Changes in this source directory need reinstalling into `/usr/local/lib` followed
by a service restart; editing the source here does not modify the root service.

The service file is a template. Use `scripts/install-fan-service.py --user USER` as root to substitute the numeric UID. Installation does not start the service. The motherboard, PWM channel, AUTO mode, NVIDIA PCI address and systemd sysfs write path are deliberately fixed to the tested hardware. Review and port those together for other hardware.
