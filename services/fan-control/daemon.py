#!/usr/bin/env python3
"""Narrow, root-owned fan controller for this desktop. Standard library only.

No arbitrary paths, commands, hardware IDs or Python imports are accepted from
clients. A manual setting is leased to the desktop heartbeat for 15 seconds.
"""
import argparse
import ctypes
import json
import logging
import math
import os
from pathlib import Path
import select
import signal
import socket
import struct
import time

SOCKET = '/run/desktop-fans/control.sock'
OWNER = int(os.environ.get('DESKTOP_FAN_UID', '1000'))
LEASE = 15
MINIMUM = {'cpu': 35, 'gpu': 30}
CRITICAL = {'cpu': 85, 'gpu': 80}
CURVES = {'cpu': [(35, 35), (50, 45), (65, 65), (75, 85), (85, 100)],
          'gpu': [(45, 30), (55, 35), (65, 50), (75, 75), (80, 100)]}


def curve_speed(device, temperature):
    points = CURVES[device]
    for (left, low), (right, high) in zip(points, points[1:]):
        if temperature <= right:
            ratio = max(0, min(1, (temperature - left) / (right - left)))
            return round(low + ratio * (high - low))
    return 100


class Hardware:
    def __init__(self):
        if Path('/sys/class/dmi/id/board_name').read_text().strip() != 'H310M-HDV/M.2':
            raise RuntimeError('Unexpected motherboard; CPU fan wiring must be configured explicitly')
        sensors = [p for p in Path('/sys/class/hwmon').glob('hwmon*')
                   if (p / 'name').read_text().strip() == 'nct6791']
        if len(sensors) != 1:
            raise RuntimeError('CPU fan sensor nct6791 is unavailable')
        self.cpu = sensors[0].resolve()
        self.lib = ctypes.CDLL('libnvidia-ml.so.1')
        self.handle = ctypes.c_void_p()
        lib = self.lib
        lib.nvmlInit_v2.argtypes = []
        lib.nvmlDeviceGetHandleByPciBusId_v2.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        lib.nvmlDeviceGetTemperature.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceGetNumFans.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        lib.nvmlDeviceSetFanSpeed_v2.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
        lib.nvmlDeviceSetDefaultFanSpeed_v2.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        self.check(lib.nvmlInit_v2())
        self.check(lib.nvmlDeviceGetHandleByPciBusId_v2(b'0000:01:00.0', ctypes.byref(self.handle)))
        fans = ctypes.c_uint()
        self.check(lib.nvmlDeviceGetNumFans(self.handle, ctypes.byref(fans)))
        if not 1 <= fans.value <= 8:
            raise RuntimeError('Invalid GPU fan count')
        self.gpu_fans = fans.value

    @staticmethod
    def check(result):
        if result != 0:
            raise RuntimeError(f'NVIDIA fan operation failed ({result})')

    def auto(self, device):
        if device == 'cpu':
            # Restore the original firmware Smart Fan IV policy. Never alter
            # BIOS temperature curves, voltage mode, or sensor mapping.
            (self.cpu / 'pwm2_enable').write_text('5\n')
        else:
            failures = []
            for index in range(self.gpu_fans):
                result = self.lib.nvmlDeviceSetDefaultFanSpeed_v2(self.handle, index)
                if result:
                    failures.append(result)
            if failures:
                self.check(failures[0])

    def set_speed(self, device, percent):
        if not MINIMUM[device] <= percent <= 100:
            raise ValueError('Fan speed is outside the permitted range')
        if device == 'cpu':
            # This chip rejects duty writes while Smart Fan is active. Enter
            # full-speed mode first, then manual, so transition duty is safe.
            if (self.cpu / 'pwm2_enable').read_text().strip() != '1':
                (self.cpu / 'pwm2_enable').write_text('0\n')
                (self.cpu / 'pwm2_enable').write_text('1\n')
            (self.cpu / 'pwm2').write_text(f'{round(percent * 255 / 100)}\n')
        else:
            for index in range(self.gpu_fans):
                self.check(self.lib.nvmlDeviceSetFanSpeed_v2(self.handle, index, percent))

    def temperature(self, device):
        if device == 'gpu':
            value = ctypes.c_uint()
            self.check(self.lib.nvmlDeviceGetTemperature(self.handle, 0, ctypes.byref(value)))
            return value.value
        values = []
        for sensor in Path('/sys/class/hwmon').glob('hwmon*'):
            if (sensor / 'name').read_text().strip() == 'coretemp':
                values.extend(int(p.read_text()) / 1000 for p in sensor.glob('temp*_input'))
        if not values:
            raise RuntimeError('CPU temperature sensor is unavailable')
        return max(values)


class Controller:
    def __init__(self, hardware, clock=time.monotonic):
        self.hardware, self.clock = hardware, clock
        self.last_heartbeat = clock()
        self.state = {device: dict(mode='auto', target=None, applied=None,
                      temperature=None, override=False, error=None, minimum=MINIMUM[device])
                      for device in MINIMUM}

    def restore(self, device, reason=None):
        state = self.state[device]
        try:
            self.hardware.auto(device)
        except Exception as error:
            # Keep the prior mode so the next tick retries restoring AUTO.
            state['error'] = str(error)
            logging.exception('Cannot restore %s AUTO', device)
            return False
        state.update(mode='auto', target=None, applied=None, override=False, error=reason)
        return True

    def restore_all(self):
        return all([self.restore(device) for device in self.state])

    def command(self, request, uid):
        if uid != OWNER:
            raise PermissionError('This socket belongs to the desktop user')
        if not isinstance(request, dict):
            raise ValueError('Expected an object')
        operation = request.get('op')
        if operation == 'heartbeat':
            self.last_heartbeat = self.clock()
        elif operation == 'set':
            device, mode = request.get('device'), request.get('mode')
            if device not in self.state or mode not in ('auto', 'full', 'silent', 'manual'):
                raise ValueError('Invalid fan or mode')
            target = None
            if mode == 'manual':
                percent = request.get('percent')
                if type(percent) is not int or not 0 <= percent <= 100:
                    raise ValueError('Expected an integer percentage between 0 and 100')
                target = max(MINIMUM[device], percent)
            self.last_heartbeat = self.clock()
            if mode == 'auto':
                if not self.restore(device):
                    raise RuntimeError(self.state[device]['error'])
            else:
                self.state[device].update(mode=mode, target=target, error=None)
                self.tick()
                if self.state[device]['error']:
                    raise RuntimeError(self.state[device]['error'])
        elif operation != 'status':
            raise ValueError('Unknown operation')
        return dict(ok=True, fans=self.state, lease_seconds=LEASE)

    def tick(self):
        now = self.clock()
        for device, state in self.state.items():
            if state['mode'] == 'auto':
                continue
            if now - self.last_heartbeat > LEASE:
                self.restore(device, 'Widget heartbeat lost: AUTO')
                continue
            try:
                temperature = self.hardware.temperature(device)
                if not math.isfinite(temperature) or not 0 <= temperature <= 125:
                    raise RuntimeError('Invalid temperature reading')
                state['temperature'] = temperature
                if temperature >= CRITICAL[device]:
                    state['override'] = True
                elif temperature <= CRITICAL[device] - 5:
                    state['override'] = False
                target = 100 if state['mode'] == 'full' or state['override'] else (
                    state['target'] if state['mode'] == 'manual' else curve_speed(device, temperature))
                # At low GPU temperatures SILENT delegates fan-stop decisions
                # to the vendor's automatic policy instead of forcing a stop.
                native = device == 'gpu' and state['mode'] == 'silent' and temperature < 45
                desired = None if native and not state['override'] else target
                if desired != state['applied']:
                    if desired is None:
                        self.hardware.auto(device)
                    else:
                        self.hardware.set_speed(device, desired)
                    state['applied'] = desired
            except Exception:
                logging.exception('Fan control failed for %s', device)
                self.restore(device, 'Sensor or control error: AUTO')


def notify(message):
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        if address.startswith('@'):
            address = '\0' + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), address)


def serve(controller):
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    path = Path(SOCKET)
    path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(SOCKET)
        os.chmod(SOCKET, 0o600)
        os.chown(SOCKET, OWNER, -1)
        server.listen(8)
        notify('READY=1')
        next_tick = 0
        while running:
            if time.monotonic() >= next_tick:
                controller.tick()
                notify('WATCHDOG=1')
                next_tick = time.monotonic() + 1
            ready, _, _ = select.select([server], [], [], .2)
            if not ready:
                continue
            connection, _ = server.accept()
            with connection:
                connection.settimeout(.2)
                try:
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != OWNER:
                        raise PermissionError('Unauthorized peer')
                    packet = bytearray()
                    while b'\n' not in packet and len(packet) <= 2048:
                        chunk = connection.recv(2049 - len(packet))
                        if not chunk:
                            break
                        packet.extend(chunk)
                    if len(packet) > 2048:
                        raise ValueError('Request too large')
                    response = controller.command(json.loads(packet), uid)
                except Exception as error:
                    response = dict(ok=False, error=str(error))
                try:
                    connection.sendall(json.dumps(response).encode() + b'\n')
                except OSError:
                    pass
    path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        hardware = Hardware()
    except Exception:
        # A failed NVIDIA initialization must not strand the independent CPU
        # fan in manual mode, including during systemd's ExecStopPost.
        if Path('/sys/class/dmi/id/board_name').read_text().strip() == 'H310M-HDV/M.2':
            for sensor in Path('/sys/class/hwmon').glob('hwmon*'):
                if (sensor / 'name').read_text().strip() == 'nct6791':
                    (sensor / 'pwm2_enable').write_text('5\n')
        raise
    controller = Controller(hardware)
    if not controller.restore_all():
        raise RuntimeError('Could not establish automatic fan control')
    if args.restore:
        return
    try:
        serve(controller)
    finally:
        controller.restore_all()


if __name__ == '__main__':
    main()
