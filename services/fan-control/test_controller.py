import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('fan_daemon', Path(__file__).with_name('daemon.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Hardware:
    def __init__(self):
        self.temp = {'cpu': 40, 'gpu': 50}
        self.applied = {'cpu': None, 'gpu': None}

    def auto(self, device):
        self.applied[device] = None

    def set_speed(self, device, percent):
        self.applied[device] = percent

    def temperature(self, device):
        value = self.temp[device]
        if isinstance(value, Exception):
            raise value
        return value


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.hw = Hardware()
        self.control = module.Controller(self.hw, lambda: self.now)

    def set(self, device, mode, **values):
        return self.control.command(dict(op='set', device=device, mode=mode, **values), module.OWNER)

    def test_manual_lower_limit_and_auto(self):
        self.set('cpu', 'manual', percent=0)
        self.assertEqual(self.hw.applied['cpu'], 35)
        self.set('cpu', 'auto')
        self.assertIsNone(self.hw.applied['cpu'])

    def test_full_both_devices(self):
        for device in ('cpu', 'gpu'):
            self.set(device, 'full')
            self.assertEqual(self.hw.applied[device], 100)

    def test_hot_override_and_hysteresis(self):
        self.set('gpu', 'manual', percent=35)
        self.hw.temp['gpu'] = 81
        self.control.tick()
        self.assertEqual(self.hw.applied['gpu'], 100)
        self.hw.temp['gpu'] = 77
        self.control.tick()
        self.assertEqual(self.hw.applied['gpu'], 100)
        self.hw.temp['gpu'] = 74
        self.control.tick()
        self.assertEqual(self.hw.applied['gpu'], 35)

    def test_expired_lease_restores_both(self):
        self.set('cpu', 'full')
        self.set('gpu', 'full')
        self.now += 16
        self.control.tick()
        self.assertEqual(self.hw.applied, {'cpu': None, 'gpu': None})
        self.assertTrue(all(s['mode'] == 'auto' for s in self.control.state.values()))

    def test_sensor_failure_restores_auto(self):
        self.set('cpu', 'manual', percent=40)
        self.hw.temp['cpu'] = OSError('sensor disappeared')
        with self.assertLogs(level='ERROR'):
            self.control.tick()
        self.assertEqual(self.control.state['cpu']['mode'], 'auto')
        self.assertIsNone(self.hw.applied['cpu'])

    def test_silent_curve_and_gpu_native_low_temperature(self):
        self.hw.temp['cpu'] = 65
        self.set('cpu', 'silent')
        self.assertEqual(self.hw.applied['cpu'], 65)
        self.hw.temp['gpu'] = 40
        self.set('gpu', 'silent')
        self.assertIsNone(self.hw.applied['gpu'])
        self.hw.temp['gpu'] = 60
        self.control.tick()
        self.assertGreater(self.hw.applied['gpu'], 30)

    def test_reject_invalid_or_unprivileged_requests(self):
        for request in (dict(op='set', device='disk', mode='full'),
                        dict(op='set', device='cpu', mode='manual', percent='100'),
                        dict(op='set', device='cpu', mode='manual', percent=101)):
            with self.assertRaises(ValueError):
                self.control.command(request, module.OWNER)
        with self.assertRaises(PermissionError):
            self.control.command(dict(op='heartbeat'), module.OWNER + 1)
        self.assertEqual(self.hw.applied, {'cpu': None, 'gpu': None})


if __name__ == '__main__':
    unittest.main()
