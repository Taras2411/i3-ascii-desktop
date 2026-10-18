"""Stable keyed Canvas and X11 desktop surface without focus ownership."""
import ctypes as c
import tkinter as tk


class StableCanvas(tk.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = {}
        self.used = set()

    def begin(self):
        self.used = set()

    def put(self, key, kind, coordinates, **options):
        self.used.add(key)
        previous = self.records.get(key)
        if previous is None:
            item = getattr(super(), 'create_' + kind)(*coordinates, **options)
            self.records[key] = [item, coordinates, options.copy(), True]
            return item
        item, old_coords, old_options, visible = previous
        if old_coords != coordinates:
            self.coords(item, *coordinates)
        changed = {k: v for k, v in options.items() if old_options.get(k) != v}
        if not visible:
            changed['state'] = 'normal'
        if changed:
            self.itemconfigure(item, **changed)
        self.records[key] = [item, coordinates, options.copy(), True]
        return item

    def end(self):
        for key, record in self.records.items():
            if key not in self.used and record[3]:
                self.itemconfigure(record[0], state='hidden')
                record[3] = False


class Surface:
    def __init__(self, root):
        self.root = root
        self.x11 = c.CDLL('libX11.so.6')
        self.shape = c.CDLL('libXext.so.6')
        self.x11.XOpenDisplay.argtypes = [c.c_char_p]
        self.x11.XOpenDisplay.restype = c.c_void_p
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('Cannot connect to X11')
        self.x11.XFlush.argtypes = [c.c_void_p]
        self.x11.XMoveWindow.argtypes = [c.c_void_p, c.c_ulong, c.c_int, c.c_int]
        self.x11.XSetWindowBackground.argtypes = [c.c_void_p, c.c_ulong, c.c_ulong]
        self.x11.XGetGeometry.argtypes = [c.c_void_p, c.c_ulong, c.POINTER(c.c_ulong),
            c.POINTER(c.c_int), c.POINTER(c.c_int)] + [c.POINTER(c.c_uint)] * 4
        self.shape.XShapeCombineRectangles.argtypes = [c.c_void_p, c.c_ulong] + [c.c_int] * 3 + [c.c_void_p] + [c.c_int] * 3
        self.frame = int(root.tk.call('wm', 'frame', root._w), 0)
        self.x11.XSetWindowBackground(self.display, self.frame, 0)
        self.last_regions = None

    def position(self, window):
        parent, x, y = c.c_ulong(), c.c_int(), c.c_int()
        width, height, border, depth = (c.c_uint() for _ in range(4))
        self.x11.XGetGeometry(self.display, window, c.byref(parent), c.byref(x), c.byref(y),
                             c.byref(width), c.byref(height), c.byref(border), c.byref(depth))
        return x.value, y.value

    def move(self, rect):
        target = rect['x'], rect['y']
        if self.position(self.frame) != target:
            self.x11.XMoveWindow(self.display, self.frame, *target)
        if self.position(self.root.winfo_id()) != (0, 0):
            self.x11.XMoveWindow(self.display, self.root.winfo_id(), 0, 0)
        self.x11.XFlush(self.display)

    def inputs(self, rectangles):
        rectangles = tuple(rectangles)
        if rectangles == self.last_regions:
            return
        class Rectangle(c.Structure):
            _fields_ = [('x', c.c_short), ('y', c.c_short), ('width', c.c_ushort), ('height', c.c_ushort)]
        array = (Rectangle * len(rectangles))(*(Rectangle(*rect) for rect in rectangles))
        self.shape.XShapeCombineRectangles(self.display, self.frame, 2, 0, 0, c.byref(array), len(array), 0, 0)
        self.x11.XFlush(self.display)
        self.last_regions = rectangles
