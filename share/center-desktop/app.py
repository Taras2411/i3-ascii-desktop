#!/usr/bin/env python3
"""Personal ASCII desktop for the central monitor of an i3/X11 session."""
from concurrent.futures import ThreadPoolExecutor
import calendar
from datetime import date, datetime, timedelta
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont
import uuid
import webbrowser

from core import (DATA, TZ, CYAN, WHITE, MUTED, GOLD, BLUE, STEPS, Focus, Music, Projects,
                  arch_mesh, render_arch, clean, read_json, write_json, event_start, event_end, events_on, event_body)
from google_calendar import GoogleCalendar, Reconnect
from surface import StableCanvas, Surface
from clocks import ClockFace, STYLES
from arch_motion import ArchMotion, matrix as arch_matrix

BORDER = '#293348'
PURPLE = '#bb9af7'
MONTHS = ['JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE', 'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER']
DAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']



class App:
    def __init__(self):
        self.state = read_json(DATA / 'state.json', {})
        self.state.setdefault('tasks', [])
        if self.state.get('clock_style') not in STYLES:
            self.state['clock_style'] = 'nixie'
        self.state.setdefault('calendar_step', 300)
        self.state.setdefault('music_step', 1)
        if self.state['music_step'] not in (.5, 1, 2, 3, 5, 10):
            self.state['music_step'] = 1
        self.music_timer = None
        if self.state['calendar_step'] not in STEPS:
            self.state['calendar_step'] = 300
        self.focus = Focus(self.state.get('focus'))
        self.month = date.today().replace(day=1)
        self.selected_day = date.today()
        self.task_offset = self.agenda_offset = 0
        self.calendar_data = read_json(DATA / 'calendar-cache.json', {})
        self.calendar_error = ''
        self.needs_auth = not (DATA / 'google-token.json').exists()
        self.cal_busy = False
        self.next_sync = 0
        self.music_data = {}
        self.project_data = []
        self.root = tk.Tk(className='CenterDesktop')
        self.root.report_callback_exception = lambda kind, value, tb: logging.error('UI callback failed', exc_info=(kind, value, tb))
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.title('ASCII Center Desktop')
        self.root.configure(bg='#000000')
        self.root.wm_attributes('-type', 'desktop')
        self.canvas = StableCanvas(self.root, bg='#000000', highlightthickness=0, takefocus=0)
        self.canvas.pack(fill='both', expand=True)
        self.clock_face = ClockFace(self.canvas)
        self.root.bind('<Configure>', self.configure)
        self.canvas.bind('<Button-1>', self.click)
        self.canvas.bind('<Button-3>', self.right_click)
        self.canvas.bind('<B1-Motion>', self.arch_drag)
        self.canvas.bind('<ButtonRelease-1>', self.arch_release)
        self.canvas.bind('<Button-4>', self.wheel)
        self.canvas.bind('<Button-5>', self.wheel)
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Motion>', self.motion)
        self.canvas.bind('<Leave>', lambda e: self.canvas.configure(cursor=''))
        self.regions = []
        self.messages = queue.Queue()
        self.executors = {name: ThreadPoolExecutor(max_workers=1, thread_name_prefix='center-' + name)
                          for name in ('calendar', 'music', 'projects', 'layout')}
        self.pending = set()
        self.music = Music()
        self.projects = Projects()
        self.google = GoogleCalendar()
        self.mesh = arch_mesh()
        self.arch = ArchMotion(self.state.get('arch_speed', 1.))
        self.covered = False
        self.dialogs = []
        self.last_frame = time.monotonic()
        self.surface = None
        self.output_rect = None
        self.geometry = None
        self.layout(initial=True)
        self.root.deiconify()
        self.root.update_idletasks()
        self.surface = Surface(self.root)
        if self.output_rect:
            self.surface.move(self.output_rect)
        self.root.lower()
        self.draw()
        self.poll()
        self.animate()
        self.refresh_music()
        self.refresh_projects()
        self.refresh_calendar()
        self.root.after(3000, self.layout)
        self.root.after(1000 - int(time.time() * 1000) % 1000, self.tick)

    def save(self):
        self.state['focus'] = self.focus.state
        write_json(DATA / 'state.json', self.state)

    def submit(self, key, pool, work, success, failure=None):
        if key in self.pending:
            return False
        self.pending.add(key)
        future = self.executors[pool].submit(work)
        future.add_done_callback(lambda future: self.messages.put((key, future, success, failure)))
        return True

    def poll(self):
        while True:
            try:
                key, future, success, failure = self.messages.get_nowait()
            except queue.Empty:
                break
            self.pending.discard(key)
            try:
                success(future.result())
            except Exception as error:
                if failure:
                    failure(error)
                else:
                    logging.exception('Background operation failed: %s', key)
        self.root.after(50, self.poll)

    def transform(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        scale = min(w / 1920, h / 1080)
        return scale, (w - 1840 * scale) / 2, (h - 1020 * scale) / 2

    def text(self, key, x, y, value, size=16, color=WHITE, anchor='nw', width=None):
        scale, ox, oy = self.transform()
        options = dict(text=value, fill=color, anchor=anchor, font=('Noto Sans Mono', -max(7, round(size * scale))))
        if width:
            options['width'] = round(width * scale)
        else:
            options['width'] = 0
        return self.canvas.put(key, 'text', (ox + x * scale, oy + y * scale), **options)

    def line(self, key, x, y, width, color=BORDER):
        scale, ox, oy = self.transform()
        self.canvas.put(key, 'line', (ox + x * scale, oy + y * scale, ox + (x + width) * scale, oy + y * scale), fill=color, dash=(2, 5), width=1)

    def region(self, name, x, y, w, h, action=None, data=None):
        self.regions.append((name, x, y, w, h, action, data))

    def button(self, key, x, y, label, action, color=CYAN, size=14):
        self.text(key, x, y, label, size, color)
        self.region(key, x - 3, y - 3, max(18, len(label) * size * .62 + 6), size + 10, action)

    def configure(self, _):
        if not hasattr(self, 'regions'):
            return
        size = self.canvas.winfo_width(), self.canvas.winfo_height()
        if size != getattr(self, '_size', None):
            self._size = size
            self.draw()
            if self.surface and self.output_rect:
                self.surface.move(self.output_rect)

    def layout(self, initial=False):
        def worker():
            outputs = json.loads(subprocess.check_output(['i3-msg', '-t', 'get_outputs'], text=True, timeout=4))
            active = sorted((o for o in outputs if o['active']), key=lambda o: o['rect']['x'])
            if not active:
                return None, False
            target = active[len(active) // 2]
            tree = json.loads(subprocess.check_output(['i3-msg', '-t', 'get_tree'], text=True, timeout=4))
            workspaces = json.loads(subprocess.check_output(['i3-msg', '-t', 'get_workspaces'], text=True, timeout=4))
            visible = next((w['name'] for w in workspaces if w['output'] == target['name'] and w['visible']), None)
            windows = []
            def walk(node, workspace=None, floating=False):
                if node.get('type') == 'workspace': workspace = node['name']
                if workspace == visible and node.get('window') and node.get('name') != 'ASCII Center Desktop':
                    windows.append(node.get('rect', {}))
                for child in node.get('nodes', []) + node.get('floating_nodes', []):walk(child, workspace)
            walk(tree)
            rect = target['rect']
            # Sample coverage on a grid: stop animation only when the whole
            # desktop is obscured, including multiple tiled application windows.
            covered = bool(windows) and all(any(r.get('x', 0) <= rect['x'] + rect['width'] * gx / 10 < r.get('x', 0) + r.get('width', 0)
                and r.get('y', 0) <= rect['y'] + rect['height'] * gy / 10 < r.get('y', 0) + r.get('height', 0)
                for r in windows) for gx in range(1, 10) for gy in range(1, 10))
            return rect, covered
        def done(result):
            rect, self.covered = result
            if not rect:
                return
            self.output_rect = rect
            geometry = f"{rect['width']}x{rect['height']}+{rect['x']}+{rect['y']}"
            if geometry != self.geometry:
                self.root.geometry(geometry)
                self.geometry = geometry
                self.root.lower()
                if self.surface:
                    self.root.after_idle(lambda: self.surface.move(rect))
        if initial:
            done(worker())
        else:
            self.submit('layout', 'layout', worker, done)
            self.root.after(3000, self.layout)

    def hit(self, event):
        scale, ox, oy = self.transform()
        if scale <= 0:
            return None
        x, y = (event.x - ox) / scale, (event.y - oy) / scale
        return next((r for r in reversed(self.regions) if r[1] <= x <= r[1] + r[3] and r[2] <= y <= r[2] + r[4]), None)

    def motion(self, event):
        self.canvas.configure(cursor='fleur' if self.arch.dragging else 'hand2' if self.hit(event) else '')

    def arch_point(self, event):
        scale, ox, oy = self.transform()
        return ((event.x-ox)/scale-920)/200, (381-(event.y-oy)/scale)/200

    def arch_drag(self, event):
        if not self.arch.dragging:return
        self.arch.drag(*self.arch_point(event), time.monotonic())
        self.canvas.configure(cursor='fleur')
        self.draw_arch()

    def arch_release(self, event):
        if self.arch.dragging:
            self.arch.end_drag(time.monotonic())
            self.canvas.configure(cursor='hand2' if self.hit(event) else '')
            self.draw_arch()

    def click(self, event):
        region = self.hit(event)
        if region and region[0] == 'arch':
            self.arch.begin_drag(*self.arch_point(event), time.monotonic())
            self.canvas.configure(cursor='fleur')
            return
        if region and region[5]:
            region[5]()

    def right_click(self, event):
        region = self.hit(event)
        if region and region[0] == 'arch':
            self.arch.toggle();self.draw_arch()
            return
        if region and region[0].startswith('task-') and region[6]:
            self.task_dialog(region[6])

    def wheel(self, event):
        region = self.hit(event)
        if not region:
            return
        direction = -1 if event.num == 4 or getattr(event, 'delta', 0) > 0 else 1
        if region[0] == 'arch':
            self.state['arch_speed'] = self.arch.change_speed(-direction)
            self.save()
            self.draw_arch()
        elif region[0] == 'cal-step':
            index = max(0, min(len(STEPS) - 1, STEPS.index(self.state['calendar_step']) + direction))
            self.state['calendar_step'] = STEPS[index]
            self.next_sync = time.monotonic() + self.state['calendar_step']
            self.save()
        elif region[0] == 'music-step':
            steps = (.5, 1, 2, 3, 5, 10)
            index = max(0, min(len(steps) - 1, steps.index(self.state['music_step']) + direction))
            self.state['music_step'] = steps[index]
            if self.music_timer is not None:
                self.root.after_cancel(self.music_timer)
            self.music_timer = self.root.after(round(self.state['music_step'] * 1000), self.refresh_music)
            self.save()
        elif region[0] == 'tasks-scroll' or (region[0].startswith('task-') and region[6]):
            self.task_offset = max(0, min(max(0, len(self.state['tasks']) - 3), self.task_offset + direction))
        elif region[0] == 'agenda-scroll' or (region[0].startswith('event-') and region[0] != 'event-add'):
            count = len(events_on(self.calendar_data.get('events', []), self.selected_day))
            self.agenda_offset = max(0, min(max(0, count - 2), self.agenda_offset + direction))
        else:
            return
        self.draw()
        return 'break'

    def tick(self):
        if self.focus.finish():
            self.save()
            text = 'Work interval complete. Time for a break.' if self.focus.state['stage'] == 'work' else 'Break complete. Ready to continue.'
            subprocess.Popen(['notify-send', 'Focus', text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if time.monotonic() >= self.next_sync and not self.cal_busy and not self.needs_auth:
            self.refresh_calendar()
        self.draw()
        self.root.after(1000 - int(time.time() * 1000) % 1000, self.tick)

    def animate(self):
        now = time.monotonic()
        if not self.covered or self.arch.dragging:
            self.arch.step(now-self.last_frame, now)
            self.draw_arch()
        self.draw_clock()
        self.last_frame = now
        self.root.after(50 if not self.covered else 250, self.animate)

    def draw_clock(self, now=None):
        if not hasattr(self, 'clock_face'):
            return
        now = now or datetime.now(TZ)
        scale, ox, oy = self.transform()
        self.clock_face.draw(self.state['clock_style'], now, ox + 25 * scale, oy, scale)

    def clock_picker(self):
        win, body = self.dialog('Clock styles / HH:MM:SS', 860, 670)
        self.label(body, 'Clock / choose a style', CYAN)
        self.label(body, 'Every style shows seconds. Your choice survives restarts.', MUTED)
        previews = []
        for style, (title, description) in STYLES.items():
            card = tk.Frame(body, bg='#000000', padx=10, pady=7)
            card.pack(fill='x', pady=6)
            preview = StableCanvas(card, width=440, height=122, bg='#000000', highlightthickness=0)
            preview.pack(side='left')
            info = tk.Frame(card, bg='#0b0e15', padx=12)
            info.pack(side='right', fill='both', expand=True)
            self.label(info, title, CYAN)
            label = self.label(info, description, MUTED)
            label.configure(wraplength=300, font=('Noto Sans Mono', 9))
            def select(style=style):
                self.state['clock_style'] = style
                self.save();win.destroy();self.draw()
            self.dialog_button(info, 'Select' + (' ✓' if self.state['clock_style'] == style else ''), select)
            preview.bind('<Button-1>', lambda _, action=select: action())
            preview.configure(cursor='hand2')
            previews.append((preview, ClockFace(preview), style))
        def frame():
            if not win.winfo_exists():
                return
            now = datetime.now(TZ)
            for canvas, face, style in previews:
                canvas.begin();face.draw(style, now, 10, 3);canvas.end()
            win.after(50, frame)
        frame()

    def draw_arch(self):
        self.text('arch-art', 920, 177, render_arch(self.mesh, orientation=arch_matrix(self.arch.orientation)), 12, '#1793d1', 'n')
        self.text('arch-hint', 920, 598, 'Drag to rotate · edge to roll · ↕ speed · right-click to pause', 11, MUTED, 'n')
        mode = {'auto':'rotating', 'drag':'manual', 'inertia':'inertia', 'return':'returning', 'paused':'paused'}[self.arch.mode]
        self.text('arch-state', 920, 620, f'[ {mode} · {self.arch.target_speed:.2g}× · {40/self.arch.target_speed:g} s/turn ]', 11, '#1793d1', 'n')

    def draw(self):
        if not hasattr(self, 'calendar_data') or self.canvas.winfo_width() < 10:
            return
        self.canvas.begin()
        self.regions = []
        now = datetime.now(TZ)
        self.draw_clock(now)
        self.region('clock-choose', 20, 0, 440, 116, self.clock_picker)
        self.text('title', 1810, 8, 'L I F E  /  D E S K', 24, WHITE, 'ne')
        self.text('date', 1810, 53, f"{DAYS[now.weekday()]}  {now:%d.%m.%Y}   ·   WEEK {now.isocalendar().week:02}", 15, MUTED, 'ne')
        self.line('top-line', 20, 118, 1800)
        self.draw_calendar(now)
        self.draw_projects()
        self.text('arch-title', 920, 145, '[ A R C H  L I N U X ]', 18, CYAN, 'n')
        self.draw_arch()
        self.region('arch', 570, 175, 700, 455)
        self.draw_tasks()
        self.draw_focus()
        self.line('music-line', 20, 656, 1800)
        self.draw_music()
        self.canvas.end()
        if self.surface:
            scale, ox, oy = self.transform()
            rectangles = [(round(ox + r[1] * scale), round(oy + r[2] * scale),
                           max(1, round(r[3] * scale)), max(1, round(r[4] * scale))) for r in self.regions]
            self.surface.inputs(rectangles)

    def draw_calendar(self, now):
        self.text('cal-title', 25, 145, '[ GOOGLE / CALENDAR ]', 16, CYAN)
        self.button('cal-settings', 345, 146, '[settings]', self.calendar_settings, MUTED, 12)
        self.button('cal-prev', 25, 180, '[<]', lambda: self.change_month(-1))
        self.text('cal-month', 245, 180, f'{MONTHS[self.month.month - 1]} {self.month.year}', 15, WHITE, 'n')
        self.button('cal-next', 435, 180, '[>]', lambda: self.change_month(1))
        for column, label in enumerate(DAYS):
            self.text(f'day-head-{column}', 45 + column * 65, 211, label, 12, MUTED)
        events = self.calendar_data.get('events', [])
        weeks = calendar.Calendar().monthdatescalendar(self.month.year, self.month.month)
        marks_key = (id(events), self.month.year, self.month.month)
        if marks_key != getattr(self, '_marks_key', None):
            self._marks_key = marks_key
            self._day_marks = {day for week in weeks for day in week if events_on(events, day)}
        for row, week in enumerate(weeks):
            for column, day in enumerate(week):
                key = f'day-{row}-{column}'
                x, y = 36 + column * 65, 237 + row * 25
                marked = day in self._day_marks
                color = GOLD if day == now.date() else WHITE if day.month == self.month.month else BORDER
                if day == self.selected_day:color = CYAN
                value = f'[{day.day:02}]' if day == self.selected_day else f' {day.day:02}' + ('*' if marked else ' ')
                self.text(key, x, y, value, 14, color)
                self.region(key, x, y - 2, 40, 23, lambda day=day: self.select_day(day))
        self.line('agenda-line', 25, 393, 455)
        if self.needs_auth:
            self.button('cal-connect', 25, 410, '[ CONNECT GOOGLE ]', self.connect_google)
            self.text('cal-explain', 25, 443, 'Sign in to display meetings and events', 12, MUTED)
        else:
            self.text('agenda-date', 25, 407, f'{self.selected_day:%d.%m} / EVENTS', 12, MUTED)
            self.button('event-add', 430, 405, '[+]', self.event_dialog)
            day_events = events_on(events, self.selected_day)
            self.region('agenda-scroll', 23, 434, 465, 57)
            for i, event in enumerate(day_events[self.agenda_offset:self.agenda_offset + 2]):
                y = 435 + i * 25
                stamp = 'ALL DAY' if 'date' in event['start'] else event_start(event).strftime('%H:%M')
                self.text(f'event-{i}', 25, y, stamp + '  ' + clean(event.get('summary', 'Untitled'), 39), 12, event.get('_color', CYAN))
                self.region(f'event-{i}', 25, y - 2, 458, 23, lambda event=event: self.event_dialog(event))
            if not day_events:
                self.text('agenda-empty', 25, 438, 'No events on this day', 12, MUTED)
        upcoming = sorted((e for e in events if event_end(e) > now and event_start(e) < now + timedelta(days=7)), key=event_start)
        if upcoming and not self.needs_auth:
            event = upcoming[0]
            delay = max(0, int((event_start(event) - now).total_seconds() / 60))
            when = 'now' if delay == 0 else f'in {delay} min' if delay < 60 else f'in {delay // 60} h' if delay < 1440 else f'in {delay // 1440} d'
            self.text('next-event', 25, 492, f'NEXT: {clean(event.get("summary"), 29)} · {when}', 11, GOLD)
        elif self.calendar_error and not self.needs_auth:
            self.text('cal-error', 25, 492, clean(self.calendar_error, 60), 11, GOLD)
        fetched = self.calendar_data.get('fetched')
        status = 'loading…' if self.cal_busy else ('offline · ' if self.calendar_error else '') + (datetime.fromtimestamp(fetched, TZ).strftime('%H:%M') if fetched else 'not connected')
        self.text('cal-sync', 25, 516, status, 10, MUTED)
        self.text('cal-step-text', 480, 516, f"step {self.state['calendar_step'] // 60} min · ↕", 10, CYAN, 'ne')
        self.region('cal-step', 328, 508, 160, 23)

    def select_day(self, day):
        changed = day.month != self.month.month or day.year != self.month.year
        self.selected_day = day
        self.month = day.replace(day=1)
        self.agenda_offset = 0
        if changed:self.refresh_calendar()
        self.draw()

    def change_month(self, delta):
        index = self.month.year * 12 + self.month.month - 1 + delta
        self.month = date(index // 12, index % 12 + 1, 1)
        self.selected_day = self.month
        self.agenda_offset = 0
        self.refresh_calendar()
        self.draw()

    def refresh_calendar(self):
        if self.cal_busy or self.needs_auth:
            return
        self.cal_busy = True
        year, month = self.month.year, self.month.month
        selected = self.state.get('calendars')
        def success(data):
            self.cal_busy = False
            self.calendar_data = data
            selection_changed = self.state.get('calendars') != selected
            if not selection_changed:
                self.state['calendars'] = data['selected']
            self.calendar_error = ''
            self.next_sync = time.monotonic() + self.state['calendar_step']
            self.save()
            if selection_changed or (year, month) != (self.month.year, self.month.month):
                self.refresh_calendar()
            self.draw()
        def failure(error):
            self.cal_busy = False
            self.needs_auth = isinstance(error, Reconnect)
            self.calendar_error = clean(error)
            self.next_sync = time.monotonic() + 60
            self.draw()
        self.submit('calendar-sync', 'calendar', lambda: self.google.sync(year, month, selected), success, failure)

    def draw_projects(self):
        self.text('projects-head', 25, 551, '[ ACTIVE PROJECTS ]', 13, CYAN)
        for i, project in enumerate(self.project_data):
            y = 579 + i * 23
            self.text(f'project-{i}', 25, y, f"{clean(project['name'], 19):19} {clean(project['branch'], 12):12} {'+' + str(project['changes']) if project['changes'] else '✓'}", 11, WHITE)
            self.region(f'project-{i}', 23, y - 2, 465, 23, lambda p=project: subprocess.Popen(['i3-sensible-terminal'], cwd=p['path']))
        if not self.project_data:
            self.text('projects-empty', 25, 582, 'Scanning project repositories…' if 'projects' in self.pending else 'No Git projects found', 12, MUTED)

    def refresh_projects(self):
        def success(data):
            self.project_data = data
            self.draw()
        self.submit('projects', 'projects', self.projects.snapshot, success)
        self.root.after(30000, self.refresh_projects)

    def draw_tasks(self):
        self.text('tasks-head', 1330, 145, '[ TOP THREE TASKS ]', 16, CYAN)
        self.button('task-add', 1770, 145, '[+]', self.task_dialog)
        tasks = sorted(self.state['tasks'], key=lambda t: bool(t.get('done')))
        self.region('tasks-scroll', 1325, 189, 490, 162)
        for i, task in enumerate(tasks[self.task_offset:self.task_offset + 3]):
            y = 195 + i * 51
            done = task.get('done')
            self.text(f'task-{i}', 1330, y, '[x]' if done else '[ ]', 17, MUTED if done else CYAN)
            self.text(f'task-text-{i}', 1380, y, clean(task['text'], 44), 14, MUTED if done else WHITE, width=420)
            self.region(f'task-{i}', 1328, y - 3, 43, 39, lambda task=task: self.toggle_task(task), task)
            self.region(f'task-edit-{i}', 1375, y - 3, 437, 43, lambda task=task: self.task_dialog(task), task)
        if not tasks:
            self.text('tasks-empty', 1330, 198, 'What matters today?\nAdd your first task with [+].', 14, MUTED)
        self.text('tasks-hint', 1330, 374, 'check to complete · click to edit · ↕ scroll', 11, MUTED)

    def toggle_task(self, task):
        task['done'] = not task.get('done', False)
        self.save()
        self.draw()

    def task_dialog(self, task=None):
        win, body = self.dialog('Task', 560, 240)
        self.label(body, 'Priority task')
        entry = self.entry(body, task['text'] if task else '')
        error = self.label(body, '', GOLD)
        def save():
            value = entry.get().strip()
            if not value:
                error.configure(text='Enter a task')
                return
            if task:task['text'] = value
            else:self.state['tasks'].append(dict(id=uuid.uuid4().hex, text=value, done=False))
            self.save();win.destroy();self.draw()
        controls = tk.Frame(body, bg='#0b0e15');controls.pack(fill='x', pady=15)
        self.dialog_button(controls, 'Save', save)
        if task:
            def delete():
                self.state['tasks'].remove(task);self.task_offset=0;self.save();win.destroy();self.draw()
            self.dialog_button(controls, 'Delete', delete)
        entry.focus_set()
        entry.bind('<Return>', lambda _: save())

    def draw_focus(self):
        self.line('focus-line', 1330, 412, 480)
        stage = self.focus.state['stage']
        self.text('focus-title', 1330, 435, '[ FOCUS / ' + ('WORK' if stage == 'work' else 'BREAK') + ' ]', 16, CYAN)
        remaining = math.ceil(self.focus.remaining())
        self.text('focus-time', 1567, 475, f'{remaining // 60:02}:{remaining % 60:02}', 54, GOLD, 'n')
        duration = 1500 if stage == 'work' else 300
        fill = round(34 * max(0, min(1, (duration - remaining) / duration)))
        self.text('focus-progress', 1330, 547, '[' + '#' * fill + '·' * (34 - fill) + ']', 20, PURPLE)
        self.button('focus-toggle', 1340, 594, '[ PAUSE ]' if self.focus.state['deadline'] else '[ START ]', self.toggle_focus)
        self.button('focus-reset', 1650, 594, '[ RESET ]', self.reset_focus, MUTED)
        self.text('focus-hint', 1330, 628, '25 minutes work / 5 minutes break', 11, MUTED)

    def toggle_focus(self):
        self.focus.toggle();self.save();self.draw()

    def reset_focus(self):
        self.focus.reset();self.save();self.draw()

    def cover_geometry(self):
        scale, _, _ = self.transform()
        size = max(7, round(10 * scale))
        if size != getattr(self, '_cover_font_size', None):
            self._cover_font_size = size
            self._cover_font = tkfont.Font(root=self.root, family='Noto Sans Mono', size=-size)
        cw = self._cover_font.measure('M')
        ch = self._cover_font.metrics('linespace')
        # Leave room for Tk text-item padding on both sides of the square.
        extent = max(1, round(288 * scale) - 4)
        return max(1, extent // cw), max(1, extent // ch), cw, ch

    def refresh_music(self):
        self.music_timer = None
        preferred = self.state.get('player')
        cover_spec = self.cover_geometry()
        def success(data):
            self.music_data = data
            self.draw()
        self.submit('music', 'music', lambda: self.music.snapshot(preferred, cover_spec), success)
        self.music_timer = self.root.after(round(self.state['music_step'] * 1000), self.refresh_music)

    def draw_music(self):
        data = self.music_data
        self.text('music-head', 25, 681, '[ NOW PLAYING / MUSIC ]', 16, CYAN)
        player = data.get('player', '')
        self.button('music-source', 1450, 681, '[ ' + clean(player or 'no source', 30) + ' ↔ ]', self.choose_player, MUTED, 12)
        art = data.get('art', [])
        spec = self.cover_geometry()
        if art and tuple(data.get('cover_spec', ())) == spec:
            cols, rows, cell_width, cell_height = spec
            scale, ox, oy = self.transform()
            left = ox + 25 * scale + (288 * scale - cols * cell_width) / 2
            top = oy + 717 * scale + (288 * scale - rows * cell_height) / 2
            for y, runs in enumerate(art):
                for i, (column, color, chars) in enumerate(runs):
                    self.canvas.put(f'cover-{y}-{i}', 'text',
                        (left + column * cell_width, top + y * cell_height),
                        text=chars, fill=color, anchor='nw', font=self._cover_font, width=0)
        else:
            placeholder = ['+----------------------------------------------+',
                           '|                                              |',
                           '|                  .--------.                  |',
                           '|               .-\'   .--.   `-.               |',
                           '|              /     ( () )     \\              |',
                           '|             |       `--\'       |             |',
                           '|              \\      /\\       /              |',
                           '|               `----/  \\----\'                |',
                           '|                  /____\\                     |',
                           '|                                              |',
                           '|            A U D I O  /  I D L E             |',
                           '|                                              |',
                           '+----------------------------------------------+']
            self.text('cover-placeholder', 25, 745, '\n'.join(placeholder), 10, BLUE)
        title = data.get('title') or 'Nothing playing yet'
        self.text('music-title', 385, 731, clean(title, 110), 29, WHITE, width=1400)
        self.text('music-artist', 385, 820, clean(data.get('artist') or 'Play music in your browser or player', 100), 20, CYAN)
        self.text('music-album', 385, 851, clean(data.get('album'), 130), 13, MUTED)
        duration, position = data.get('duration', 0), data.get('position', 0)
        if data.get('status') == 'Playing':
            position += max(0, time.monotonic() - data.get('stamp', time.monotonic()))
        if duration:
            position = min(position, duration)
        fraction = position / duration if duration else 0
        fill = round(104 * fraction)
        self.text('music-progress', 385, 894, '[' + '━' * fill + '·' * (104 - fill) + ']', 21, BLUE)
        capabilities = data.get('capabilities', {})
        controllable = bool(player) and capabilities.get('CanControl', True)
        if duration and controllable and capabilities.get('CanSeek', True):
            self.region('music-seek', 385, 891, 1330, 32, data=duration)
        def clock(value):
            return f'{int(value) // 60:02}:{int(value) % 60:02}'
        self.text('music-time', 1810, 932, clock(position) + ' / ' + (clock(duration) if duration else 'LIVE'), 14, MUTED, 'ne')
        controls = [('prev', 385, '[ << ]', 'previous', 'CanGoPrevious'),
                    ('play', 570, '[ PAUSE ]' if data.get('status') == 'Playing' else '[ PLAY ]', 'play-pause', 'CanPause' if data.get('status') == 'Playing' else 'CanPlay'),
                    ('next', 870, '[ >> ]', 'next', 'CanGoNext')]
        for key, x, label, action, capability in controls:
            if controllable and capabilities.get(capability, True):
                self.button('music-' + key, x, 948, label, lambda action=action: self.music_control(action), size=20)
            else:
                self.text('music-' + key, x, 948, label, 20, MUTED)
        self.text('music-status', 385, 1000, clean(getattr(self, 'music_error', ''), 100) or 'MPRIS  /  color ASCII album art', 10, MUTED)
        self.text('music-step', 1810, 1000, f"step {self.state['music_step']:g} s · ↕", 10, CYAN, 'ne')
        self.region('music-step', 1650, 990, 170, 26)

    def music_control(self, action, value=None):
        player = self.music_data.get('player')
        if not player:
            return
        from core import command
        args = ['playerctl', '-p', player, action] + ([] if value is None else [str(value)])
        def done(_):
            self.music_error = ''
            self.refresh_music_once()
        def failed(_):
            self.music_error = 'This source does not support that action'
            self.draw()
        self.submit('music-control', 'music', lambda: command(args), done, failed)

    def refresh_music_once(self):
        def done(data):self.music_data=data;self.draw()
        cover_spec = self.cover_geometry()
        self.submit('music', 'music', lambda: self.music.snapshot(self.state.get('player'), cover_spec), done)

    def choose_player(self):
        win, body = self.dialog('Music source', 560, 300)
        self.dialog_button(body, 'Automatic: currently playing source', lambda: self.set_player(None, win))
        for player in self.music_data.get('players', []):
            self.dialog_button(body, clean(player), lambda p=player: self.set_player(p, win))
        if not self.music_data.get('players'):self.label(body, 'No players found yet', MUTED)

    def set_player(self, player, win):
        self.state['player'] = player;self.save();win.destroy();self.refresh_music_once()

    def dialog(self, title, width=640, height=500):
        win = tk.Toplevel(self.root, class_='CenterDesktop')
        win.withdraw()
        win.title(title)
        win.configure(bg='#0b0e15')
        win.wm_attributes('-type', 'dialog')
        rect = self.output_rect or dict(x=0, y=0, width=1920, height=1080)
        win.geometry(f"{width}x{height}+{rect['x'] + (rect['width'] - width) // 2}+{rect['y'] + (rect['height'] - height) // 2}")
        body = tk.Frame(win, bg='#0b0e15', padx=22, pady=18)
        body.pack(fill='both', expand=True)
        win.bind('<Escape>', lambda _: win.destroy())
        win.deiconify()
        win.lift()
        win.focus_set()
        self.dialogs.append(win)
        self.dialogs = [d for d in self.dialogs if d.winfo_exists()]
        return win, body

    def label(self, parent, text, color=WHITE):
        label = tk.Label(parent, text=text, bg='#0b0e15', fg=color, font=('Noto Sans Mono', 11), anchor='w', justify='left', wraplength=640)
        label.pack(fill='x', pady=(3, 5))
        return label

    def entry(self, parent, value=''):
        entry = tk.Entry(parent, bg='#151b28', fg=WHITE, insertbackground=CYAN, relief='flat', font=('Noto Sans Mono', 11))
        entry.insert(0, value)
        entry.pack(fill='x', ipady=6, pady=(0, 7))
        return entry

    def dialog_button(self, parent, label, action):
        button = tk.Button(parent, text=label, command=action, bg='#182533', fg=CYAN, activebackground='#25374b', activeforeground=WHITE,
                           relief='flat', padx=12, pady=7, font=('Noto Sans Mono', 10), cursor='hand2')
        button.pack(side='top', anchor='w', pady=4)
        return button

    def connect_google(self):
        win, body = self.dialog('Connect Google Calendar', 730, 420)
        self.label(body, 'Sign in with Google / OAuth', CYAN)
        self.label(body, 'A Desktop app client JSON with Calendar API enabled is required.\nOpen the setup guide using the button below.', MUTED)
        status = self.label(body, '', GOLD)
        def start(path=None):
            status.configure(text='Waiting for browser sign-in… (up to 3 minutes)')
            button.configure(state='disabled')
            def done(_):
                self.needs_auth=False;self.calendar_error='';self.state['calendars']=None;self.calendar_data={};self.save();win.destroy();self.refresh_calendar()
            def failed(error):
                if win.winfo_exists():
                    status.configure(text=clean(error, 260));button.configure(state='normal')
            if not self.submit('calendar-connect', 'calendar', lambda: self.google.connect(path), done, failed):
                failed(ValueError('Google sign-in is already open in the browser'))
        def choose():
            path = filedialog.askopenfilename(parent=win, title='OAuth JSON / Desktop app', filetypes=[('JSON', '*.json')])
            if path:start(path)
        button = self.dialog_button(body, 'Import OAuth JSON and sign in', choose)
        if self.google.client.exists():
            self.dialog_button(body, 'Sign in with the saved OAuth client', start)
        self.dialog_button(body, 'Open setup guide', lambda: subprocess.Popen(['xdg-open', str(DATA / 'GOOGLE-SETUP.html')]))

    def calendar_settings(self):
        win, body = self.dialog('Google calendars', 720, 600)
        if self.calendar_error:self.label(body, self.calendar_error, GOLD)
        self.dialog_button(body, 'Connect / switch account', lambda: (win.destroy(), self.connect_google()))
        variables = []
        viewport = tk.Frame(body, bg='#0b0e15')
        viewport.pack(fill='both', expand=True, pady=8)
        listing = tk.Canvas(viewport, bg='#0b0e15', highlightthickness=0, height=230)
        scrollbar = tk.Scrollbar(viewport, orient='vertical', command=listing.yview)
        listing.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y');listing.pack(side='left', fill='both', expand=True)
        choices = tk.Frame(listing, bg='#0b0e15')
        window_id = listing.create_window((0, 0), window=choices, anchor='nw')
        choices.bind('<Configure>', lambda _: listing.configure(scrollregion=listing.bbox('all')))
        listing.bind('<Configure>', lambda event: listing.itemconfigure(window_id, width=event.width))
        def scroll(event):
            listing.yview_scroll(-1 if event.num == 4 else 1, 'units')
        for widget in (listing, choices):
            widget.bind('<Button-4>', scroll);widget.bind('<Button-5>', scroll)
        for cal in self.calendar_data.get('calendars', []):
            variable = tk.BooleanVar(value=cal['id'] in (self.state.get('calendars') or []))
            label = cal.get('summary', cal['id']) + (' · read-only' if cal.get('accessRole') not in ('owner', 'writer') else '')
            check = tk.Checkbutton(choices, text=label, variable=variable, bg='#0b0e15', fg=cal.get('backgroundColor', CYAN),
                                   selectcolor='#151b28', activebackground='#0b0e15', activeforeground=WHITE, font=('Noto Sans Mono', 10), anchor='w')
            check.pack(fill='x');variables.append((cal['id'], variable))
            check.bind('<Button-4>', scroll);check.bind('<Button-5>', scroll)
        def save():
            self.state['calendars'] = [key for key, variable in variables if variable.get()]
            self.save();win.destroy();self.refresh_calendar()
        if variables:self.dialog_button(body, 'Save calendar selection', save)
        self.dialog_button(body, 'Refresh now', lambda: (win.destroy(), self.refresh_calendar()))
        if (DATA / 'pending-event.json').exists():
            status = self.label(body, 'An event creation is pending', GOLD)
            def recover():
                def done(_):
                    if win.winfo_exists():status.configure(text='Write recovered')
                    self.refresh_calendar()
                def failed(error):
                    if win.winfo_exists():status.configure(text=clean(error))
                self.submit('calendar-recover', 'calendar', lambda: self.google.resolve_pending(self.google.api(), retry=True), done, failed)
            self.dialog_button(body, 'Check and finish previous write', recover)
        self.label(body, 'Tasks on the right are local and separate from Google Tasks.', MUTED)

    def event_dialog(self, event=None):
        calendars = self.calendar_data.get('calendars', [])
        writable = [cal for cal in calendars if cal.get('accessRole') in ('owner', 'writer')]
        if not event and not writable:
            messagebox.showinfo('Calendar', 'No writable calendars. Select an account in settings.', parent=self.root)
            return
        win, body = self.dialog('Google Calendar event', 750, 790)
        can_edit = not event or event.get('_writable', False)
        if event and event.get('recurringEventId'):
            self.label(body, 'Recurring event: changes apply only to this occurrence.', GOLD)
        if event and not can_edit:self.label(body, 'This event is read-only.', GOLD)
        choices = writable if not event else [c for c in calendars if c['id'] == event['_calendar']]
        self.label(body, 'Calendar', MUTED)
        calendar_box = ttk.Combobox(body, values=[clean(c.get('summary', c['id'])) for c in choices], state='readonly')
        calendar_box.pack(fill='x', pady=4)
        if choices:calendar_box.current(0)
        if event:calendar_box.configure(state='disabled')
        self.label(body, 'Title', MUTED)
        title = self.entry(body, event.get('summary', '') if event else '')
        all_day = tk.BooleanVar(value=bool(event and 'date' in event['start']))
        tk.Checkbutton(body, text='All day · end date inclusive', variable=all_day,
                       bg='#0b0e15', fg=WHITE, selectcolor='#151b28', activebackground='#0b0e15').pack(anchor='w')
        default = datetime.combine(self.selected_day, datetime.now(TZ).replace(minute=0, second=0).time(), TZ)
        start = event_start(event) if event else default
        end = event_end(event) if event else default + timedelta(hours=1)
        self.label(body, 'Start: YYYY-MM-DD HH:MM · Europe/Prague', MUTED)
        first = self.entry(body, start.strftime('%Y-%m-%d') if all_day.get() else start.strftime('%Y-%m-%d %H:%M'))
        self.label(body, 'End: YYYY-MM-DD HH:MM · Europe/Prague', MUTED)
        last = self.entry(body, (end.date() - timedelta(days=1)).isoformat() if all_day.get() else end.strftime('%Y-%m-%d %H:%M'))
        def change_format(*_):
            for entry, stamp in ((first, start), (last, end)):
                value = entry.get()
                entry.delete(0, 'end')
                entry.insert(0, value[:10] if all_day.get() else value[:10] + ' ' + stamp.strftime('%H:%M'))
        all_day.trace_add('write', change_format)
        self.label(body, 'Location', MUTED)
        location = self.entry(body, event.get('location', '') if event else '')
        self.label(body, 'Description', MUTED)
        description = tk.Text(body, height=4, bg='#151b28', fg=WHITE, insertbackground=CYAN, relief='flat', font=('Noto Sans Mono', 10))
        description.pack(fill='x')
        description.insert('1.0', event.get('description', '') if event else '')
        status = self.label(body, '', GOLD)
        operation_id = uuid.uuid4().hex
        controls = tk.Frame(body, bg='#0b0e15');controls.pack(fill='x', pady=5)
        buttons = []
        def perform(operation):
            if operation == 'delete' and not messagebox.askyesno('Delete event?', 'Delete this event from Google Calendar?' + ('\nOnly the selected occurrence.' if event and event.get('recurringEventId') else ''), parent=win):
                return
            try:
                payload = None if operation == 'delete' else event_body(title.get(), first.get(), last.get(), all_day.get(), location.get(), description.get('1.0', 'end-1c'))
                calendar_id = event['_calendar'] if event else choices[calendar_box.current()]['id']
            except (ValueError, IndexError) as error:
                status.configure(text=str(error));return
            for button in buttons:button.configure(state='disabled')
            status.configure(text='Saving…')
            def done(_):
                if win.winfo_exists():win.destroy()
                self.refresh_calendar()
            def failed(error):
                if win.winfo_exists():
                    status.configure(text=clean(error, 220))
                    for button in buttons:button.configure(state='normal')
            if not self.submit('calendar-mutate', 'calendar', lambda: self.google.mutate(operation, calendar_id, payload, event, operation_id), done, failed):
                failed(ValueError('Wait for the previous write to finish'))
        if can_edit:
            buttons.append(self.dialog_button(controls, 'Save', lambda: perform('patch' if event else 'insert')))
            if event:buttons.append(self.dialog_button(controls, 'Delete', lambda: perform('delete')))
        else:
            for entry in (title, first, last, location):entry.configure(state='disabled')
            description.configure(state='disabled')
        if event:
            self.dialog_button(controls, 'Open in Google Calendar' + (' / entire series' if event.get('recurringEventId') else ''), lambda: webbrowser.open(event.get('htmlLink', 'https://calendar.google.com/')))
        title.focus_set()


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA, 0o700)
    logging.basicConfig(filename=DATA / 'center.log', level=logging.WARNING)
    lock = (DATA / 'widget.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    app = App()
    # Seek position is obtained from the actual pointer coordinate.
    original_click = app.click
    def click(event):
        region = app.hit(event)
        if region and region[0] == 'music-seek' and region[6]:
            scale, ox, _ = app.transform()
            fraction = max(0, min(1, ((event.x - ox) / scale - region[1]) / region[3]))
            app.music_control('position', round(region[6] * fraction, 3))
        else:original_click(event)
    app.canvas.bind('<Button-1>', click)
    if '--clock-picker' in sys.argv:
        app.root.after(500, app.clock_picker)
    app.root.mainloop()


if __name__ == '__main__':
    main()
