"""Live clock faces with cached, supersampled DSKY segment rendering."""
import math
from functools import lru_cache
import time

STYLES = {
    'nixie': ('Nixie tubes', 'Warm glow, glass tubes and mesh.'),
    'flip': ('Split-flap display', 'Bright digits and vertically flipping cards.'),
    'apollo': ('Apollo / electroluminescent', 'Slanted EL digits, separate segments and a soft green glow.'),
}
SEGMENTS = {'0':'abcedf','1':'bc','2':'abged','3':'abgcd','4':'fgbc',
            '5':'afgcd','6':'afgecd','7':'abc','8':'abcdefg','9':'abfgcd'}


def clip_y(points, low, high):
    """Clip polygon to a card half before applying hinge perspective."""
    for bound, above in ((low, True), (high, False)):
        output = []
        if not points:
            break
        previous = points[-1]
        pin = previous[1] >= bound if above else previous[1] <= bound
        for current in points:
            cin = current[1] >= bound if above else current[1] <= bound
            if cin != pin:
                ratio = (bound - previous[1]) / (current[1] - previous[1])
                output.append((previous[0] + ratio * (current[0] - previous[0]), bound))
            if cin:
                output.append(current)
            previous, pin = current, cin
        points = output
    return points


def segment_shapes(x, y, w, h):
    t = w * .13
    def horizontal(yy):
        return [(x+t,yy),(x+w-t,yy),(x+w,yy+t/2),(x+w-t,yy+t),(x+t,yy+t),(x,yy+t/2)]
    def vertical(xx, yy, height):
        return [(xx,yy+t),(xx+t/2,yy),(xx+t,yy+t),(xx+t,yy+height-t),(xx+t/2,yy+height),(xx,yy+height-t)]
    return dict(a=horizontal(y), g=horizontal(y+(h-t)/2), d=horizontal(y+h-t),
                f=vertical(x,y,h/2), b=vertical(x+w-t,y,h/2),
                e=vertical(x,y+h/2,h/2), c=vertical(x+w-t,y+h/2,h/2))


def dsky_shapes(x, y, w, h, lean=.18):
    """Slanted EL numerals with separated, chamfered segment electrodes.

    Adapted from the DSKY figure in NASA's Computers in Spaceflight, p. 54:
    https://www.nasa.gov/wp-content/uploads/static/history/computers/p54.jpg
    The clock keeps its HH:MM:SS layout rather than simulating AGC registers.
    """
    thickness, gap = w * .095, w * .035
    bevel = thickness * .34
    def bar(left, top, width, height):
        cut = min(bevel, width / 3, height / 3)
        return [(left+cut,top),(left+width-cut,top),
                (left+width,top+cut),(left+width,top+height-cut),
                (left+width-cut,top+height),(left+cut,top+height),
                (left,top+height-cut),(left,top+cut)]
    mid = (h-thickness)/2
    upper_top, upper_bottom = thickness+gap, mid-gap
    lower_top, lower_bottom = mid+thickness+gap, h-thickness-gap
    horizontal_left, horizontal_width = x+thickness+gap, w-2*(thickness+gap)
    polygons = {
        'a':bar(horizontal_left,y,horizontal_width,thickness),
        'g':bar(horizontal_left,y+mid,horizontal_width,thickness),
        'd':bar(horizontal_left,y+h-thickness,horizontal_width,thickness),
        'f':bar(x,y+upper_top,thickness,upper_bottom-upper_top),
        'b':bar(x+w-thickness,y+upper_top,thickness,upper_bottom-upper_top),
        'e':bar(x,y+lower_top,thickness,lower_bottom-lower_top),
        'c':bar(x+w-thickness,y+lower_top,thickness,lower_bottom-lower_top),
    }
    return {name:[(px+(y+h-py)*lean,py) for px,py in points] for name,points in polygons.items()}


@lru_cache(maxsize=120)
def apollo_digit_image(digit, scale=1, supersample=4):
    """Rasterize at 4x output resolution, then filter down to display pixels.

    Every size is rendered from geometry; previously reduced images are never
    enlarged. Solid panel backing avoids dark/transparent compositing fringes.
    """
    from PIL import Image, ImageDraw
    size = max(1, round(56*scale)), max(1, round(72*scale))
    image = Image.new('RGB',(size[0]*supersample,size[1]*supersample),'#080e09')
    draw = ImageDraw.Draw(image)
    sx, sy = image.width/56, image.height/72
    for name,points in dsky_shapes(4,3,35,66).items():
        active = name in SEGMENTS[digit]
        draw.polygon([(x*sx,y*sy) for x,y in points],
                     fill='#b8ed9a' if active else '#0d1b10',
                     outline='#355031' if active else None,
                     width=max(1,round(supersample*scale)))
    return image.resize(size,Image.Resampling.LANCZOS)


class ClockFace:
    width, height = 430, 112

    def __init__(self, canvas, prefix='clock', image_factory=None):
        self.canvas, self.prefix = canvas, prefix
        self.last = None
        self.previous = None
        self.changed = 0
        self.keys = set()
        self.used = set()
        self.style = None
        self.signature = None
        self.visible_keys = set()
        self.image_factory = image_factory
        self.digit_images = {}

    def put(self, key, kind, coords, **options):
        key = self.prefix + ':' + key
        self.used.add(key)
        if kind == 'text':
            family, size = options.pop('font', ('Noto Sans Mono', 12))
            options['font'] = (family, -max(6, round(size * self.scale)))
            options.setdefault('anchor', 'nw')
            options.setdefault('width', 0)
        scaled = tuple((self.x if index % 2 == 0 else self.y) + value * self.scale for index, value in enumerate(coords))
        return self.canvas.put(key, kind, scaled, **options)

    def text(self, key, x, y, value, color, size=12, anchor='nw'):
        self.put(key, 'text', (x,y), text=value, fill=color, font=('Noto Sans Mono', size), anchor=anchor)

    def rect(self, key, x, y, w, h, fill, outline='', width=1):
        self.put(key, 'rectangle', (x,y,x+w,y+h), fill=fill, outline=outline, width=max(.5,width*self.scale))

    def line(self, key, points, fill, width=1):
        self.put(key, 'line', tuple(v for point in points for v in point), fill=fill, width=max(.5,width*self.scale))

    def polygon(self, key, points, fill, outline=''):
        if len(points) >= 3:
            self.put(key, 'polygon', tuple(v for point in points for v in point), fill=fill, outline=outline)

    def draw(self, style, now, x=0, y=0, scale=1, monotonic=None):
        stamp = now.strftime('%H:%M:%S')
        mono = time.monotonic() if monotonic is None else monotonic
        if style != self.style:
            self.last = self.previous = stamp
            self.style = style
        if self.last != stamp:
            self.previous, self.last, self.changed = self.last, stamp, mono
        progress = max(0,min(1,(mono-self.changed)/.42))
        animating = style == 'flip' and progress < 1 and self.previous != stamp
        signature = (style, stamp, x, y, scale, round(progress, 3) if animating else 1)
        if signature == self.signature:
            self.canvas.used.update(self.visible_keys)
            return animating
        self.signature = signature
        self.scale, self.x, self.y = scale, x, y
        self.used = set()
        getattr(self, style)(stamp, self.previous or stamp, progress)
        # Animation frames update only this clock's owned items. Other desktop
        # elements and their stacking order remain intact.
        for key in self.keys - self.used:
            record = self.canvas.records.get(key)
            if record and record[3]:
                self.canvas.itemconfigure(record[0], state='hidden')
                record[3] = False
        self.keys.update(self.used)
        self.visible_keys = self.used.copy()
        return style == 'flip' and progress < 1 and self.previous != stamp

    def digit_positions(self, stamp):
        x = 8
        for index, char in enumerate(stamp):
            yield index, char, x
            x += 20 if char == ':' else 60

    def nixie(self, stamp, previous, progress):
        for index,char,x in self.digit_positions(stamp):
            key = f'n{index}'
            if char == ':':
                self.text(key+'colon',x+7,39,':','#d76b2a',26,'n')
                continue
            self.polygon(key+'glass',[(x+5,9),(x+13,3),(x+41,3),(x+49,9),(x+52,21),(x+52,84),(x+46,94),(x+8,94),(x+2,84),(x+2,21)],'#100b07','#50351e')
            self.line(key+'shine',[(x+10,20),(x+10,74)],'#61462a')
            self.text(key+'mesh',x+15,16,': : :\n: : :\n: : :\n: : :\n: : :','#332011',10)
            self.text(key+'off',x+28,18,'8','#2b170e',56,'n')
            for suffix,dx,dy,color in [('halo',0,1,'#743014'),('left',-1,0,'#b74818'),('right',1,0,'#b74818'),('lit',0,0,'#ffad64')]:
                self.text(key+suffix,x+28+dx,18+dy,char,color,56,'n')
            self.rect(key+'base',x+1,92,53,7,'#352719','#6b4a2b')
            for pin in range(4):
                self.line(key+f'pin{pin}',[(x+15+pin*8,100),(x+15+pin*8,104)],'#805e36')
        self.text('n-label',202,106,'H H     M M     S S   ·   [ style ]','#8d663d',8,'n')

    def segments(self,key,digit,x,y,w,h,color,dim=None,clip=None,hinge=None):
        for name,points in segment_shapes(x,y,w,h).items():
            active = name in SEGMENTS[digit]
            if not active and dim is None:
                continue
            if clip:
                points = clip_y(points,*clip)
            if hinge:
                line,factor = hinge
                points = [(px,line+(py-line)*factor) for px,py in points]
            self.polygon(key+name,points,color if active else dim)

    def flip(self, stamp, previous, progress):
        for index,char,x in self.digit_positions(stamp):
            key = f'f{index}'
            if char == ':':
                self.text(key+'colon',x+7,33,':','#b8c1d2',30,'n')
                continue
            self.rect(key+'case',x,4,55,94,'#141922','#3a4251')
            self.rect(key+'upper',x+3,7,49,43,'#202630')
            self.rect(key+'lower',x+3,51,49,44,'#191f29')
            digit_x,digit_y,w,h=x+12,17,31,66
            changed = char != previous[index] and progress < 1
            if changed:
                # New upper/old lower halves under the moving card.
                self.segments(key+'static-top',char,digit_x,digit_y,w,h,'#e4e8ef',clip=(0,51))
                self.segments(key+'static-bottom',previous[index],digit_x,digit_y,w,h,'#b6bfcd',clip=(51,100))
                if progress < .5:
                    factor = math.cos(progress*math.pi)
                    self.rect(key+'moving-top-bg',x+3,51-44*factor,49,44*factor,'#202630')
                    self.segments(key+'moving-top',previous[index],digit_x,digit_y,w,h,'#e4e8ef',clip=(0,51),hinge=(51,factor))
                else:
                    factor = math.sin((progress-.5)*math.pi)
                    self.rect(key+'moving-bottom-bg',x+3,51,49,44*factor,'#191f29')
                    self.segments(key+'moving-bottom',char,digit_x,digit_y,w,h,'#b6bfcd',clip=(51,100),hinge=(51,factor))
            else:
                self.segments(key+'static-top',char,digit_x,digit_y,w,h,'#e4e8ef',clip=(0,51))
                self.segments(key+'static-bottom',char,digit_x,digit_y,w,h,'#b6bfcd',clip=(51,100))
            self.line(key+'seam',[(x+2,51),(x+53,51)],'#07090c',2)
            self.rect(key+'hinge-left',x,47,4,9,'#586170')
            self.rect(key+'hinge-right',x+51,47,4,9,'#586170')
        self.text('f-label',202,106,'H H     M M     S S   ·   [ style ]','#737e9c',8,'n')

    def apollo(self, stamp, previous, progress):
        self.rect('a-panel',0,0,410,103,'#080e09','#394638')
        self.rect('a-inner',3,17,404,80,'#080e09','#17251a')
        self.text('a-heading',15,5,'LOCAL TIME   /   PRG','#5a9d71',8)
        self.text('a-run',392,5,'RUN','#b8ed9a',8,'ne')
        for index,char,x in self.digit_positions(stamp):
            key=f'a{index}'
            if char==':':
                for j,y in enumerate((43,66)):
                    self.rect(key+f'dot{j}',x+6,y,3,3,'#b8ed9a')
                continue
            image_key = (char, round(self.scale, 4))
            if image_key not in self.digit_images:
                image = apollo_digit_image(char, image_key[1])
                if self.image_factory:
                    photo = self.image_factory(image)
                else:
                    from PIL import ImageTk
                    photo = ImageTk.PhotoImage(image, master=self.canvas)
                self.digit_images[image_key] = photo
            self.put(key+'smooth', 'image', (x,20), image=self.digit_images[image_key], anchor='nw')
        self.text('a-label',202,106,'DSKY  /  H H   M M   S S   ·   [ style ]','#5a9d71',8,'n')
