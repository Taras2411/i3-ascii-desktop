"""Behavior tests: persisted focus, real-world event dates, uncertain writes."""
from datetime import date, datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httplib2
from googleapiclient.errors import HttpError
import core
from core import Focus, event_body, events_on, event_start, arch_mesh, render_arch, Music
import google_calendar as gc


class FocusTests(unittest.TestCase):
    def test_restart_preserves_deadline_and_requires_manual_next_stage(self):
        with patch('core.time.time', return_value=1000):
            timer = Focus();timer.toggle()
        saved = json.loads(json.dumps(timer.state))
        with patch('core.time.time', return_value=1400):
            restored = Focus(saved)
            self.assertEqual(restored.remaining(), 1100)
            restored.toggle()
            self.assertEqual(restored.state['remaining'], 1100)
        with patch('core.time.time', return_value=2000):restored.toggle()
        with patch('core.time.time', return_value=3100):
            self.assertTrue(restored.finish())
            self.assertFalse(restored.finish())
            self.assertIsNone(restored.state['deadline'])
            restored.toggle()
            self.assertEqual(restored.state['stage'], 'break')
            self.assertEqual(restored.remaining(), 300)


class EventTests(unittest.TestCase):
    def test_all_day_end_is_exclusive_and_spans_days(self):
        body = event_body('Trip', '2026-10-18', '2026-10-20', True)
        self.assertEqual(body['end']['date'], '2026-10-21')
        self.assertEqual(len(events_on([body], date(2026, 10, 20))), 1)
        self.assertEqual(len(events_on([body], date(2026, 10, 21))), 0)

    def test_dst_offsets_and_invalid_spring_time(self):
        winter = event_body('Winter', '2026-12-01 10:00', '2026-12-01 11:00')
        summer = event_body('Summer', '2026-06-01 10:00', '2026-06-01 11:00')
        self.assertTrue(winter['start']['dateTime'].endswith('+01:00'))
        self.assertTrue(summer['start']['dateTime'].endswith('+02:00'))
        with self.assertRaises(ValueError):event_body('Invalid', '2026-03-29 02:30', '2026-03-29 04:00')

    def test_utc_event_crosses_local_date_boundary(self):
        event = dict(start={'dateTime':'2026-10-18T23:30:00Z'}, end={'dateTime':'2026-10-19T00:30:00Z'})
        self.assertEqual(event_start(event).date(), date(2026,10,19))
        self.assertFalse(events_on([event], date(2026,10,18)))
        self.assertEqual(len(events_on([event], date(2026,10,19))),1)

    def test_empty_title_and_reverse_times(self):
        with self.assertRaises(ValueError):event_body(' ', '2026-10-18 10:00', '2026-10-18 11:00')
        with self.assertRaises(ValueError):event_body('Bad', '2026-10-18 11:00', '2026-10-18 10:00')


class FakeRequest:
    def __init__(self, action):self.action=action;self.headers={}
    def execute(self, **_):return self.action()


class FakeEvents:
    def __init__(self):self.rows={};self.insertions=0;self.drop_response=True;self.etags=[]
    def get(self, calendarId, eventId):
        def action():
            if eventId not in self.rows:raise HttpError(httplib2.Response({'status':'404'}), b'{}')
            return self.rows[eventId]
        return FakeRequest(action)
    def insert(self, calendarId, body):
        def action():
            self.insertions+=1;self.rows[body['id']]=body
            if self.drop_response:self.drop_response=False;raise OSError('response lost')
            return body
        return FakeRequest(action)
    def patch(self, **kwargs):
        request=FakeRequest(lambda:kwargs['body']);self.last_request=request;return request
    def delete(self, **kwargs):
        def action():raise HttpError(httplib2.Response({'status':'404'}),b'{}')
        request=FakeRequest(action);self.last_request=request;return request


class GoogleTests(unittest.TestCase):
    def test_lost_insert_response_is_reconciled_after_restart(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(gc,'DATA',Path(directory)):
            backend=gc.GoogleCalendar();events=FakeEvents()
            api=type('API',(),{'events':lambda _:events})()
            backend.api=lambda:api
            payload=event_body('Safe', '2026-10-18 10:00','2026-10-18 11:00')
            with self.assertRaises(gc.CalendarError):backend.mutate('insert','primary',payload,operation_id='same')
            self.assertEqual(events.insertions,1)
            restored=gc.GoogleCalendar();restored.api=lambda:api
            result=restored.mutate('insert','primary',payload,operation_id='same')
            self.assertEqual(result['summary'],'Safe')
            self.assertEqual(events.insertions,1)
            self.assertFalse((Path(directory)/'pending-event.json').exists())

    def test_etag_protects_edits_and_delete_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(gc,'DATA',Path(directory)):
            backend=gc.GoogleCalendar();events=FakeEvents();api=type('API',(),{'events':lambda _:events})()
            backend.api=lambda:api
            event={'id':'event','etag':'"version1"'}
            backend.mutate('patch','primary',{'summary':'changed'},event)
            self.assertEqual(events.last_request.headers['If-Match'],'"version1"')
            self.assertIsNone(backend.mutate('delete','primary',event=event))


class CalendarSyncTests(unittest.TestCase):
    def test_calendar_pages_occurrences_colors_and_read_only_permissions(self):
        class API:
            def __init__(self): self.event_calls=[]
            def calendarList(self): return self
            def events(self): return Events(self)
            def list(self, pageToken=None):
                if not pageToken:
                    return FakeRequest(lambda: {'items':[{'id':'owned','primary':True,'accessRole':'owner','backgroundColor':'#123456'}], 'nextPageToken':'next'})
                return FakeRequest(lambda: {'items':[{'id':'shared','accessRole':'reader','backgroundColor':'#654321'}]})
        class Events:
            def __init__(self,api):self.api=api
            def list(self,**kwargs):
                self.api.event_calls.append(kwargs)
                event={'id':kwargs['calendarId'],'summary':'Occurrence',
                       'start':{'dateTime':'2026-10-18T10:00:00+02:00'},
                       'end':{'dateTime':'2026-10-18T11:00:00+02:00'},'recurringEventId':'series'}
                return FakeRequest(lambda:{'items':[event,dict(event,id='cancelled',status='cancelled')]})
        with tempfile.TemporaryDirectory() as directory,patch.object(gc,'DATA',Path(directory)):
            backend=gc.GoogleCalendar();api=API();backend.api=lambda:api
            result=backend.sync(2026,10,['owned','shared'])
            self.assertEqual(len(result['calendars']),2)
            self.assertEqual(len(result['events']),2)
            self.assertTrue(result['events'][0]['_writable'])
            self.assertFalse(result['events'][1]['_writable'])
            self.assertEqual(result['events'][1]['_color'],'#654321')
            self.assertTrue(all(call['singleEvents'] for call in api.event_calls))
            self.assertTrue((Path(directory)/'calendar-cache.json').exists())

    def test_different_form_does_not_duplicate_uncertain_creation(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(gc,'DATA',Path(directory)):
            backend=gc.GoogleCalendar();events=FakeEvents();api=type('API',(),{'events':lambda _:events})();backend.api=lambda:api
            payload=event_body('Safe','2026-10-18 10:00','2026-10-18 11:00')
            with self.assertRaises(gc.CalendarError):backend.mutate('insert','primary',payload,operation_id='first')
            with self.assertRaises(gc.CalendarError):backend.mutate('insert','primary',payload,operation_id='new-form')
            self.assertEqual(events.insertions,1)


class RenderingTests(unittest.TestCase):
    def test_arch_is_bounded_and_changes_in_three_dimensions(self):
        mesh=arch_mesh()
        front,side=render_arch(mesh,0),render_arch(mesh,1.5708)
        self.assertEqual(len(front.splitlines()),24)
        self.assertTrue(all(len(line)==88 for line in front.splitlines()))
        self.assertNotEqual(front,side)
        self.assertGreater(sum(ch!=' ' for ch in front),sum(ch!=' ' for ch in side))

    def test_cover_has_color_ascii_and_cache_survives_source_removal(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory,patch.object(core,'DATA',Path(directory)):
            image=Image.new('RGB',(100,100))
            image.putdata([(x*2,y*2,80) for y in range(100) for x in range(100)])
            path=Path(directory)/'cover.png';image.save(path)
            music=Music();rows=music.cover(path.as_uri())
            self.assertEqual(len(rows),20)
            self.assertEqual(sum(len(run[2]) for run in rows[0]),48)
            self.assertGreater(len({run[1] for row in rows for run in row}),1)
            self.assertEqual(music.cover(path.as_uri()),rows)

    def test_cover_keeps_all_four_edges_without_cropping(self):
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as directory,patch.object(core,'DATA',Path(directory)):
            image=Image.new('RGB',(200,200),'white');draw=ImageDraw.Draw(image)
            for box,color in [((0,0,18,199),'red'),((181,0,199,199),'lime'),((0,0,199,18),'blue'),((0,181,199,199),'yellow')]:
                draw.rectangle(box,fill=color)
            path=Path(directory)/'edges.png';image.save(path)
            rows=Music().cover(path.as_uri())
            colors={tuple(int(run[1][i:i+2],16) for i in (1,3,5)) for row in rows for run in row if run[2].strip()}
            for dominant in (lambda r,g,b:r>200 and g<100 and b<100,
                             lambda r,g,b:g>200 and r<100 and b<100,
                             lambda r,g,b:b>200 and r<100 and g<100,
                             lambda r,g,b:r>200 and g>200 and b<100):
                self.assertTrue(any(dominant(*color) for color in colors))

    def test_circle_is_round_in_physical_character_cells(self):
        from PIL import Image,ImageDraw
        with tempfile.TemporaryDirectory() as directory,patch.object(core,'DATA',Path(directory)):
            image=Image.new('RGB',(200,200),'black')
            ImageDraw.Draw(image).ellipse((20,20,180,180),fill='white')
            path=Path(directory)/'circle.png';image.save(path)
            rows=Music().cover(path.as_uri())
            points=[]
            for y,row in enumerate(rows):
                for x,color,text in row:
                    if int(color[1:3],16)>180:
                        points.extend((x+i,y) for i,ch in enumerate(text) if ch!=' ')
            width=(max(x for x,y in points)-min(x for x,y in points)+1)*6
            height=(max(y for x,y in points)-min(y for x,y in points)+1)*14
            self.assertLess(abs(width/height-1),.12)

    def test_private_state_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json';core.write_json(path,{'tasks':['one']})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(core.read_json(path,{}),{'tasks':['one']})

class ClockTests(unittest.TestCase):
    class Canvas:
        def __init__(self):self.records={};self.used=set();self.writes=0
        def put(self,key,kind,coords,**options):
            self.used.add(key);self.writes+=1
            if key not in self.records:self.records[key]=[len(self.records)+1,coords,options,True]
            else:self.records[key][1:]=[coords,options,True]
        def itemconfigure(self,item,**options):pass

    def test_seconds_and_midnight_rollover_in_all_faces(self):
        from clocks import ClockFace,STYLES
        for style in STYLES:
            canvas=self.Canvas();face=ClockFace(canvas,image_factory=lambda image:image)
            face.draw(style,datetime(2026,10,18,23,59,59),monotonic=10)
            face.draw(style,datetime(2026,10,19,0,0,0),monotonic=11)
            self.assertEqual(face.last,'00:00:00')
            self.assertEqual(face.previous,'23:59:59')
            if style=='nixie':self.assertEqual(canvas.records['clock:n7lit'][2]['text'],'0')

    def test_dsky_edges_are_antialiased_at_native_output_size(self):
        from clocks import apollo_digit_image
        smooth=apollo_digit_image('8');jagged=apollo_digit_image('8',supersample=1)
        self.assertEqual(smooth.size,(56,72))
        self.assertNotEqual(smooth.tobytes(),jagged.tobytes())
        self.assertGreater(len(smooth.getcolors(10000)),100)
        self.assertEqual(smooth.getpixel((0,0)),(8,14,9))
        self.assertEqual(apollo_digit_image('8',scale=1.5).size,(84,108))
        self.assertIs(smooth,apollo_digit_image('8'))

    def test_dsky_numerals_slant_right_without_tilting_baseline(self):
        from clocks import dsky_shapes,segment_shapes
        shapes=dsky_shapes(0,0,35,66)
        top=shapes['a'];bottom=shapes['d']
        center=lambda points:sum(x for x,y in points)/len(points)
        self.assertGreater(center(top)-center(bottom),10)
        self.assertAlmostEqual(min(y for x,y in top),0)
        self.assertAlmostEqual(max(y for x,y in bottom),66)
        self.assertLess(max(x for points in shapes.values() for x,y in points),48)
        self.assertNotEqual(shapes,segment_shapes(0,0,35,66))
        self.assertLess(max(y for x,y in shapes['f']),min(y for x,y in shapes['g']))
        self.assertLess(max(y for x,y in shapes['g']),min(y for x,y in shapes['e']))

    def test_static_frames_reuse_items_and_register_visibility(self):
        from clocks import ClockFace
        canvas=self.Canvas();face=ClockFace(canvas,image_factory=lambda image:image);now=datetime(2026,10,18,12,34,56)
        face.draw('apollo',now,monotonic=10);writes=canvas.writes
        canvas.used.clear();face.draw('apollo',now,monotonic=10.1)
        self.assertEqual(canvas.writes,writes)
        self.assertEqual(canvas.used,face.visible_keys)

    def test_flip_completes_and_hides_moving_halves(self):
        from clocks import ClockFace
        canvas=self.Canvas();face=ClockFace(canvas,image_factory=lambda image:image)
        face.draw('flip',datetime(2026,10,18,12,34,58),monotonic=10)
        self.assertTrue(face.draw('flip',datetime(2026,10,18,12,34,59),monotonic=11))
        face.draw('flip',datetime(2026,10,18,12,34,59),monotonic=11.3)
        self.assertTrue(any('moving-bottom' in key and canvas.records[key][3] for key in face.visible_keys))
        self.assertFalse(face.draw('flip',datetime(2026,10,18,12,34,59),monotonic=11.5))
        self.assertFalse(any('moving' in key for key in face.visible_keys))
        self.assertTrue(all(not record[3] for key,record in canvas.records.items() if 'moving' in key))

class ArchMotionTests(unittest.TestCase):
    def test_arcball_can_rotate_around_all_three_axes(self):
        from arch_motion import ArchMotion,multiply,conjugate
        for start,end,axis in [((0,0),(.5,0),2),((0,0),(0,.5),1),((1,0),(0,1),3)]:
            motion=ArchMotion();initial=motion.orientation
            motion.begin_drag(*start,0);motion.drag(*end,.1)
            delta=multiply(motion.orientation,conjugate(initial))
            self.assertGreater(abs(delta[axis]),.2)

    def test_release_inertia_then_smoothly_returns_to_moving_auto_target(self):
        from arch_motion import ArchMotion,distance
        motion=ArchMotion();motion.begin_drag(0,0,0);motion.drag(.65,.35,.1);motion.end_drag(.11)
        before=motion.orientation;motion.step(.05,.16)
        self.assertGreater(distance(before,motion.orientation),.01)
        modes=set();last=motion.orientation
        for frame in range(1,240):
            motion.step(.05,.16+frame*.05);modes.add(motion.mode)
            self.assertLess(distance(last,motion.orientation),.5)
            last=motion.orientation
        self.assertIn('return',modes);self.assertEqual(motion.mode,'auto')
        self.assertLess(distance(motion.orientation,motion.target()),1e-6)
        before=motion.orientation;motion.step(.05,12.2)
        self.assertGreater(distance(before,motion.orientation),.001)

    def test_holding_still_cancels_stale_fling_velocity(self):
        from arch_motion import ArchMotion
        motion=ArchMotion();motion.begin_drag(0,0,0);motion.drag(.6,.3,.1);motion.end_drag(1.1)
        self.assertLess(sum(v*v for v in motion.velocity),1e-6)

    def test_wheel_limits_and_speed_easing(self):
        from arch_motion import ArchMotion
        motion=ArchMotion()
        for _ in range(100):motion.change_speed(1)
        self.assertEqual(motion.target_speed,8)
        motion.step(.05,.05)
        self.assertGreater(motion.speed,1);self.assertLess(motion.speed,8)
        for _ in range(100):motion.change_speed(-1)
        self.assertEqual(motion.target_speed,.1)
        for value in ('bad',None,float('nan'),float('inf')):
            self.assertEqual(ArchMotion(value).speed,1)

    def test_orientation_stays_orthonormal_and_normalizes_after_opposite_drag(self):
        import numpy as np
        from arch_motion import ArchMotion,matrix
        motion=ArchMotion();motion.begin_drag(1,0,0);motion.drag(-1,0,.1);motion.end_drag(.12)
        for i in range(150):motion.step(.05,.12+(i+1)*.05)
        rotation=np.array(matrix(motion.orientation))
        self.assertTrue(np.all(np.isfinite(rotation)))
        self.assertTrue(np.allclose(rotation.T@rotation,np.eye(3),atol=1e-10))
        self.assertAlmostEqual(np.linalg.det(rotation),1)

    def test_pause_does_not_jump_on_resume(self):
        from arch_motion import ArchMotion,distance
        motion=ArchMotion();motion.step(.05,.05);motion.toggle();before=motion.orientation
        motion.step(.05,1)
        self.assertEqual(before,motion.orientation)
        motion.toggle();motion.step(.05,1.05)
        self.assertLess(distance(before,motion.orientation),.03)
        self.assertGreater(distance(before,motion.orientation),0)

    def test_rotated_renderer_preserves_bounded_ascii_frame(self):
        import math
        from arch_motion import matrix,rotation
        from core import arch_mesh,render_arch
        mesh=arch_mesh();outputs=[]
        for axis in ((1,0,0),(0,1,0),(0,0,1),(1,1,1)):
            art=render_arch(mesh,orientation=matrix(rotation(axis,math.pi*.7)))
            rows=art.splitlines();outputs.append(art)
            self.assertEqual(len(rows),24);self.assertTrue(all(len(row)==88 for row in rows))
            self.assertTrue(rows[0].isspace() and rows[-1].isspace())
            self.assertTrue(all(row[0]==' ' and row[-1]==' ' for row in rows))
        self.assertEqual(len(set(outputs)),4)

if __name__=='__main__':unittest.main()
