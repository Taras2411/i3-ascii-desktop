"""Quaternion arcball, damped release inertia and continuous autopilot return."""
import math


def normalize(q):
    length = math.sqrt(sum(v*v for v in q))
    return tuple(v/length for v in q) if length > 1e-12 else (1.,0.,0.,0.)


def multiply(a, b):
    w,x,y,z = a
    v,i,j,k = b
    return (w*v-x*i-y*j-z*k, w*i+x*v+y*k-z*j,
            w*j-x*k+y*v+z*i, w*k+x*j-y*i+z*v)


def conjugate(q):
    return q[0],-q[1],-q[2],-q[3]


def rotation(axis, angle):
    length = math.sqrt(sum(v*v for v in axis))
    if length < 1e-12:
        return (1.,0.,0.,0.)
    sine = math.sin(angle/2)/length
    return (math.cos(angle/2),*(v*sine for v in axis))


def between(a, b):
    dot = max(-1.,min(1.,sum(x*y for x,y in zip(a,b))))
    if dot < -.999999:
        # Opposite arcball points: choose a stable perpendicular axis.
        basis = (1.,0.,0.) if abs(a[0]) < .8 else (0.,1.,0.)
        axis = (a[1]*basis[2]-a[2]*basis[1], a[2]*basis[0]-a[0]*basis[2], a[0]*basis[1]-a[1]*basis[0])
        return rotation(axis,math.pi)
    return normalize((1+dot,a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]))


def slerp(a, b, fraction):
    dot = sum(x*y for x,y in zip(a,b))
    if dot < 0:
        b = tuple(-v for v in b)
        dot = -dot
    dot = min(1.,dot)
    if dot > .9995:
        return normalize(tuple(x+(y-x)*fraction for x,y in zip(a,b)))
    angle = math.acos(dot)
    scale = math.sin(angle)
    first,second = math.sin((1-fraction)*angle)/scale, math.sin(fraction*angle)/scale
    return normalize(tuple(x*first+y*second for x,y in zip(a,b)))


def distance(a, b):
    return 2*math.acos(min(1.,abs(sum(x*y for x,y in zip(a,b)))))


def matrix(q):
    w,x,y,z = normalize(q)
    return ((1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)),
            (2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)),
            (2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)))


def ball_point(x, y):
    length = x*x+y*y
    if length <= 1:
        return x,y,math.sqrt(1-length)
    factor = 1/math.sqrt(length)
    return x*factor,y*factor,0.


class ArchMotion:
    minimum, maximum = .1, 8.

    def __init__(self, speed=1.):
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            speed = 1.
        if not math.isfinite(speed):speed=1.
        self.speed = self.target_speed = max(self.minimum,min(self.maximum,speed))
        self.base = rotation((1,0,0),.12)
        self.phase = 0.
        self.orientation = self.base
        self.velocity = (0.,0.,0.)
        self.mode = 'auto'
        self.enabled = True
        self.dragging = False
        self.last_point = None
        self.last_drag = 0.
        self.released = 0.

    def change_speed(self, direction):
        self.target_speed = max(self.minimum,min(self.maximum,self.target_speed*1.25**direction))
        if abs(self.target_speed-1) < .015:self.target_speed=1.
        return self.target_speed

    def begin_drag(self, x, y, now):
        self.dragging,self.enabled,self.mode = True,True,'drag'
        self.last_point = ball_point(x,y)
        self.last_drag = now
        self.velocity = (0.,0.,0.)

    def drag(self, x, y, now):
        if not self.dragging:return
        point = ball_point(x,y)
        delta = between(self.last_point,point)
        dt = max(.001,now-self.last_drag)
        if delta[0] < 0:delta=tuple(-v for v in delta)
        sine = math.sqrt(sum(v*v for v in delta[1:]))
        if sine < 1e-8:
            return
        angle = 2*math.atan2(sine,delta[0])
        instantaneous = tuple(v/sine*min(8.,angle/dt) for v in delta[1:])
        alpha = 1-math.exp(-dt/.06)
        self.velocity = tuple(old+(new-old)*alpha for old,new in zip(self.velocity,instantaneous))
        self.orientation = normalize(multiply(delta,self.orientation))
        self.last_point,self.last_drag = point,now

    def end_drag(self, now):
        if not self.dragging:return
        self.dragging = False
        self.mode,self.released = 'inertia',now
        # Holding still before release must not launch an old fling.
        decay = math.exp(-max(0.,now-self.last_drag-.04)/.09)
        self.velocity = tuple(v*decay for v in self.velocity)
        self.align_phase()

    def align_phase(self):
        relative = multiply(conjugate(self.base),self.orientation)
        if math.hypot(relative[0],relative[2]) > 1e-8:
            self.phase = 2*math.atan2(relative[2],relative[0])

    def toggle(self):
        self.dragging = False
        self.enabled = not self.enabled
        self.velocity = (0.,0.,0.)
        self.align_phase()
        self.mode = 'return' if self.enabled else 'paused'

    def target(self):
        return normalize(multiply(self.base,rotation((0,1,0),self.phase)))

    def step(self, dt, now):
        dt = max(0.,min(.15,dt))
        self.speed += (self.target_speed-self.speed)*(1-math.exp(-dt/.4))
        if self.dragging or not self.enabled or dt == 0:
            return
        amount = math.tau/40*self.speed*dt
        self.phase = (self.phase+amount)%math.tau
        if self.mode == 'auto':
            self.orientation = self.target()
            return
        if self.mode == 'inertia' and now-self.released >= 1.1:
            self.mode = 'return'
        # The same auto increment advances both current and target orientations
        # during return, so convergence has no permanent lag or final snap.
        if self.mode == 'return':
            increment = multiply(multiply(self.base,rotation((0,1,0),amount)),conjugate(self.base))
            self.orientation = normalize(multiply(increment,self.orientation))
        decay = math.exp(-dt/0.55)
        speed = math.sqrt(sum(v*v for v in self.velocity))
        if speed > 1e-5:
            angle = speed*.55*(1-decay)
            self.orientation = normalize(multiply(rotation(self.velocity,angle),self.orientation))
        self.velocity = tuple(v*decay for v in self.velocity)
        if self.mode == 'return':
            target = self.target()
            self.orientation = slerp(self.orientation,target,1-math.exp(-dt/0.75))
            if distance(self.orientation,target) < .0001 and speed < .001:
                self.orientation,self.mode,self.velocity = target,'auto',(0.,0.,0.)
