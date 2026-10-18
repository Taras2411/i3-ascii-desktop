"""Local state, 3D ASCII geometry and background desktop data sources."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_common.i18n import tr, get_language
import calendar
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import math
import os
import re
import subprocess
import time
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

HOME = Path.home()
DATA = HOME / '.local/share/center-desktop'
TZ = ZoneInfo('Europe/Prague')
CYAN, WHITE, MUTED, GOLD, BLUE = '#73daca', '#c0caf5', '#737e9c', '#e0af68', '#7aa2f7'
STEPS = (60, 120, 300, 600, 900, 1800)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def command(args, timeout=5):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=timeout).rstrip('\n')


def clean(value, limit=200):
    return ' '.join(str(value or '').split())[:limit]


class Focus:
    def __init__(self, saved=None):
        self.state = saved or dict(stage='work', remaining=1500, deadline=None)
        self.state.setdefault('stage', 'work')
        self.state.setdefault('remaining', 1500)
        self.state.setdefault('deadline', None)

    def remaining(self, now=None):
        deadline = self.state['deadline']
        return max(0, deadline - (time.time() if now is None else now)) if deadline else self.state['remaining']

    def toggle(self):
        if self.state['deadline']:
            self.state['remaining'] = self.remaining()
            self.state['deadline'] = None
        else:
            if self.state['remaining'] <= 0:
                self.state['stage'] = 'break' if self.state['stage'] == 'work' else 'work'
                self.state['remaining'] = 300 if self.state['stage'] == 'break' else 1500
            self.state['deadline'] = time.time() + self.state['remaining']

    def reset(self):
        self.state.update(stage='work', remaining=1500, deadline=None)

    def finish(self):
        if self.state['deadline'] and self.remaining() <= 0:
            self.state.update(deadline=None, remaining=0)
            return True
        return False


def event_start(event):
    value = event.get('start', {})
    if 'dateTime' in value:
        stamp = datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00'))
        return stamp.replace(tzinfo=TZ) if stamp.tzinfo is None else stamp.astimezone(TZ)
    return datetime.combine(date.fromisoformat(value['date']), datetime.min.time(), TZ)


def event_end(event):
    value = event.get('end', event.get('start', {}))
    if 'dateTime' in value:
        stamp = datetime.fromisoformat(value['dateTime'].replace('Z', '+00:00'))
        return stamp.replace(tzinfo=TZ) if stamp.tzinfo is None else stamp.astimezone(TZ)
    return datetime.combine(date.fromisoformat(value['date']), datetime.min.time(), TZ)


def events_on(events, day):
    lo = datetime.combine(day, datetime.min.time(), TZ)
    hi = datetime.combine(day + timedelta(days=1), datetime.min.time(), TZ)
    return sorted((event for event in events if event.get('status') != 'cancelled'
                   and event_start(event) < hi and event_end(event) > lo),
                  key=lambda event: ('dateTime' in event.get('start', {}), event_start(event)))


def event_body(title, start, end, all_day=False, location='', description=''):
    if not title.strip():
        raise ValueError(tr('An event title is required'))
    if all_day:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if last < first:
            raise ValueError(tr('End date precedes start date'))
        timing = {'start': {'date': first.isoformat()}, 'end': {'date': (last + timedelta(days=1)).isoformat()}}
    else:
        first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
        for stamp in (first, last):
            local = stamp.replace(tzinfo=TZ)
            # Reject nonexistent local times during the spring DST transition.
            if local.timestamp() != local.astimezone(ZoneInfo('UTC')).astimezone(TZ).timestamp() or local.astimezone(ZoneInfo('UTC')).astimezone(TZ).replace(tzinfo=None) != stamp:
                raise ValueError(tr('This local time does not exist due to daylight saving time'))
        first, last = first.replace(tzinfo=TZ), last.replace(tzinfo=TZ)
        if last.timestamp() <= first.timestamp():
            raise ValueError(tr('End must be after start'))
        timing = {'start': {'dateTime': first.isoformat(), 'timeZone': 'Europe/Prague'},
                  'end': {'dateTime': last.isoformat(), 'timeZone': 'Europe/Prague'}}
    return dict(summary=title.strip(), location=location.strip(), description=description, **timing)


def arch_mesh():
    """Sample a thick Arch silhouette, including its arched cutout and slashes."""
    n = 115
    def inside(x, y):
        t = (y + 1.3) / 2.6
        if not 0 <= t <= 1 or abs(x) > 1.18 * (1 - t):
            return False
        # The arch opening is rounded at its crown and open at the base.
        if y < -.73 and abs(x) < .31:
            return False
        if -.73 <= y <= -.23 and (x / .31)**2 + ((y + .73) / .50)**2 < 1:
            return False
        # Arch's asymmetric diagonal incision near its upper left flank.
        if x < -.08 and .40 < y < .53 - .48 * x:
            return False
        # Small notch on the lower right flank.
        if x > .53 and -.84 < y < -.76 - .21 * (x - .53):
            return False
        return True
    points = []
    delta = 2.6 / n
    for iy in range(n + 1):
        y = -1.3 + iy * delta
        for ix in range(n + 1):
            x = -1.3 + ix * delta
            if not inside(x, y):
                continue
            points.append((x, y, .19, 0, 0, 1))
            points.append((x, y, -.19, 0, 0, -1))
            edges = [(dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                     if not inside(x + dx * delta, y + dy * delta)]
            for dx, dy in edges:
                for iz in range(15):
                    points.append((x, y, -.19 + iz * .38 / 14, dx, dy, 0))
    import numpy as np
    return np.array(points)


def render_arch(mesh, angle=0, cols=88, rows=24, orientation=None):
    """Vectorized point rasterization with a per-character depth buffer."""
    import numpy as np
    mesh = np.asarray(mesh)
    x, y, z, nx, ny, nz = mesh.T
    if orientation is None:
        co, si = math.cos(angle), math.sin(angle)
        ct, st = math.cos(.12), math.sin(.12)
        orientation = ((co,0,si),(st*si,ct,-st*co),(-ct*si,st,ct*co))
        fit = 1.
    else:
        # A fixed bounding sphere keeps every corner in view during arbitrary
        # pitch/roll, without zoom pulsing as the mouse turns the model.
        radius = float(np.sqrt(np.max(x*x+y*y+z*z)))
        projected_radius = radius/math.sqrt(1-(radius/4.8)**2)
        fit = min(1.,(rows-2)/2/(projected_radius*((rows-2)/2.9)))
    def rotated(a,b,c):
        return tuple(a*row[0]+b*row[1]+c*row[2] for row in orientation)
    rx,ry,rz = rotated(x,y,z)
    perspective = 4.8 / (4.8 - rz)
    px = np.rint(cols / 2 + rx * perspective * 22*fit).astype(int)
    py = np.rint(rows / 2 - ry * perspective * ((rows - 2) / 2.9)*fit).astype(int)
    valid = (px >= 0) & (px < cols) & (py >= 0) & (py < rows)
    indices = py[valid] * cols + px[valid]
    depth = np.full(cols * rows, -np.inf)
    np.maximum.at(depth, indices, rz[valid])
    visible = rz[valid] >= depth[indices]
    normal_x,normal_y,normal_z = rotated(nx,ny,nz)
    light = np.maximum(0, -.35 * normal_x + .45 * normal_y + .82 * normal_z)
    light = np.clip(light * (1 - .10 * x + .06 * y), 0, 1)
    ramp = np.array(list('.:-=+*#%@'))
    shade = np.minimum(len(ramp) - 1, ((.18 + .82 * light) * (len(ramp) - 1)).astype(int))
    cells = np.full(cols * rows, ' ')
    cells[indices[visible]] = ramp[shade[valid][visible]]
    return '\n'.join(''.join(row) for row in cells.reshape(rows, cols))


class Music:
    def __init__(self):
        self.art = {}
        self.failures = {}
        self.capabilities = {}
        self.capability_stamp = 0
        self.capability_player = None
        self.cache = DATA / 'covers'
        self.cache.mkdir(exist_ok=True)

    def snapshot(self, preferred=None, cover_spec=(48, 20, 6, 14)):
        try:
            players = command(['playerctl', '-l']).splitlines()
        except (OSError, subprocess.SubprocessError):
            return dict(players=[], status='Stopped', art=[])
        statuses = {}
        for player in players:
            try:
                statuses[player] = command(['playerctl', '-p', player, 'status'])
            except subprocess.SubprocessError:
                pass
        chosen = preferred if preferred in players else next((p for p in players if statuses.get(p) == 'Playing'), players[0])
        sep = '\x1f'
        fmt = sep.join('{{' + key + '}}' for key in ('xesam:title', 'xesam:artist', 'xesam:album', 'mpris:artUrl', 'mpris:length', 'mpris:trackid'))
        try:
            fields = command(['playerctl', '-p', chosen, 'metadata', '--format', fmt]).split(sep)
            fields += [''] * (6 - len(fields))
            try:
                position = float(command(['playerctl', '-p', chosen, 'position']))
            except (ValueError, subprocess.SubprocessError):
                position = 0
            try:
                duration = float(fields[4]) / 1e6
            except ValueError:
                duration = 0
            if chosen != self.capability_player or time.monotonic() - self.capability_stamp > 5:
                self.capability_player, self.capability_stamp = chosen, time.monotonic()
                try:
                    properties = command(['gdbus', 'call', '--session', '--dest', 'org.mpris.MediaPlayer2.' + chosen,
                        '--object-path', '/org/mpris/MediaPlayer2', '--method', 'org.freedesktop.DBus.Properties.GetAll',
                        'org.mpris.MediaPlayer2.Player'])
                    self.capabilities = {key: value == 'true' for key, value in re.findall(r"'(Can\w+)': <(true|false)>", properties)}
                except (OSError, subprocess.SubprocessError):
                    self.capabilities = {}
            return dict(players=players, player=chosen, status=statuses.get(chosen, 'Paused'), capabilities=self.capabilities,
                        title=clean(fields[0]) or tr('Untitled'), artist=clean(fields[1]), album=clean(fields[2]),
                        duration=duration, position=position, stamp=time.monotonic(), track=fields[5], cover_spec=cover_spec, art=self.cover(fields[3], cover_spec))
        except (OSError, subprocess.SubprocessError):
            return dict(players=players, player=chosen, status='Stopped', art=[])

    def cover(self, url, spec=(48, 20, 6, 14)):
        if not url:
            return []
        parsed = urlparse(url)
        if parsed.scheme == 'file':
            try:
                identity = url + ':' + str(Path(unquote(parsed.path)).stat().st_mtime_ns)
            except OSError:
                return []
        else:
            identity = url
        cols, rows, cell_width, cell_height = spec
        # Cache identity includes the rendering algorithm and real font metrics.
        identity = 'cover-v2:' + ':'.join(map(str, spec)) + ':' + identity
        if identity in self.art:
            return self.art[identity]
        if time.monotonic() - self.failures.get(identity, -100) < 60:
            return []
        from PIL import Image, ImageOps
        result = []
        try:
            parsed = urlparse(url)
            cache = self.cache / (hashlib.sha256(identity.encode()).hexdigest() + '.json')
            if cache.exists():
                result = read_json(cache, [])
            else:
                if parsed.scheme == 'file':
                    path = Path(unquote(parsed.path))
                    if path.stat().st_size > 10 * 1024 * 1024:
                        return []
                    payload = path.read_bytes()
                elif parsed.scheme in ('http', 'https'):
                    with urlopen(Request(url, headers={'User-Agent': 'CenterDesktop/1.0'}), timeout=6) as response:
                        payload = response.read(10 * 1024 * 1024 + 1)
                else:
                    return []
                if len(payload) > 10 * 1024 * 1024:
                    return []
                source = ImageOps.exif_transpose(Image.open(io.BytesIO(payload))).convert('RGB')
                # Fit the whole source into the physical display rectangle BEFORE
                # reducing to character cells. A grid's aspect ratio is not the
                # displayed image's aspect ratio: characters are taller than wide.
                physical_size = (cols * cell_width, rows * cell_height)
                fitted = ImageOps.pad(source, physical_size, method=Image.Resampling.LANCZOS, color=(0, 0, 0))
                pixels = fitted.resize((cols, rows), Image.Resampling.LANCZOS)
                image = pixels.quantize(colors=16, dither=Image.Dither.NONE)
                ramp = ' .,:;irsXA253hMHGS#9B&@'
                rgb = image.convert('RGB')
                for y in range(rows):
                    runs = []
                    for x in range(cols):
                        r, g, b = rgb.getpixel((x, y))
                        luma = .2126 * r + .7152 * g + .0722 * b
                        char = ramp[max(1, min(len(ramp) - 1, round(luma / 255 * (len(ramp) - 1))))]
                        if not any(pixels.getpixel((x, y))):
                            char = ' '
                        # Lift dark pixels enough to remain visible against black.
                        color = '#%02x%02x%02x' % tuple(min(255, round(v * .85 + 32)) for v in (r, g, b))
                        if runs and runs[-1][1] == color:
                            runs[-1][2] += char
                        else:
                            runs.append([x, color, char])
                    result.append(runs)
                write_json(cache, result)
                old = sorted(self.cache.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
                for path in old[32:]:
                    path.unlink(missing_ok=True)
        except Exception:
            # Cover failures never stop playback controls or metadata.
            result = []
        if not result:
            self.failures[identity] = time.monotonic()
            if len(self.failures) > 32:
                self.failures.pop(next(iter(self.failures)))
            return []
        self.art[identity] = result
        if len(self.art) > 32:
            self.art.pop(next(iter(self.art)))
        return result


class Projects:
    def __init__(self):
        self.paths = []
        self.scanned = 0

    def snapshot(self):
        if time.monotonic() - self.scanned > 300 or not self.scanned:
            self.paths = []
            excluded = {'node_modules', 'venv', '.venv', 'env', 'target', 'build', 'dist', '__pycache__'}
            root = Path(os.environ.get('DESKTOP_PROJECTS_DIR', str(HOME / '1work'))).expanduser()
            for folder, dirs, files in os.walk(root):
                rel = Path(folder).relative_to(root)
                dirs[:] = [d for d in dirs if d not in excluded and not d.startswith('.')]
                if (Path(folder) / '.git').exists():
                    self.paths.append(Path(folder))
                if len(rel.parts) >= 3:
                    dirs[:] = []
            self.scanned = time.monotonic()
        records = []
        for path in self.paths:
            try:
                git = ['git', '--no-optional-locks', '-C', str(path)]
                # NUL delimiting preserves file names containing spaces/newlines.
                raw = command(git + ['status', '--porcelain=v1', '-z', '--untracked-files=normal'])
                entries = raw.split('\0') if raw else []
                files, index = [], 0
                while index < len(entries):
                    record = entries[index]
                    if len(record) >= 4:
                        files.append(record[3:])
                        if 'R' in record[:2] or 'C' in record[:2]:
                            index += 1
                    index += 1
                try:
                    branch = command(git + ['symbolic-ref', '--short', 'HEAD'])
                except subprocess.SubprocessError:
                    branch = command(git + ['rev-parse', '--short', 'HEAD'])
                try:
                    activity = int(command(git + ['log', '-1', '--format=%ct']))
                except (ValueError, subprocess.SubprocessError):
                    activity = 0
                for filename in files:
                    try:
                        activity = max(activity, (path / filename).stat().st_mtime)
                    except OSError:
                        pass
                gitdir = Path(command(git + ['rev-parse', '--absolute-git-dir']))
                for filename in ('logs/HEAD', 'HEAD'):
                    try:
                        activity = max(activity, (gitdir / filename).stat().st_mtime)
                    except OSError:
                        pass
                records.append(dict(path=str(path), name=path.name, branch=branch, changes=len(files), activity=activity))
            except (OSError, subprocess.SubprocessError):
                continue
        return sorted(records, key=lambda r: r['activity'], reverse=True)[:3]
