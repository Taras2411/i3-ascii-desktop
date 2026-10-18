"""Google Calendar API; all methods run on one background executor."""
import copy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import socket
import uuid

from core import DATA, TZ, read_json, write_json

SCOPES = ['https://www.googleapis.com/auth/calendar.calendarlist.readonly',
          'https://www.googleapis.com/auth/calendar.events']


class CalendarError(Exception):
    pass


class Reconnect(CalendarError):
    pass


class GoogleCalendar:
    def __init__(self):
        self.service = None
        self.credentials = None
        self.token = DATA / 'google-token.json'
        self.client = DATA / 'google-client.json'
        self.pending = read_json(DATA / 'pending-event.json', None)

    def save_token(self):
        if self.credentials:
            write_json(self.token, json.loads(self.credentials.to_json()))

    def connect(self, client_path=None):
        from google_auth_oauthlib.flow import InstalledAppFlow
        if client_path:
            content = json.loads(Path(client_path).read_text())
            if 'installed' not in content:
                raise CalendarError('An OAuth Desktop app JSON is required; Web application clients are unsupported')
            write_json(self.client, content)
        if not self.client.exists():
            raise CalendarError('Import a Desktop app OAuth client JSON first')
        flow = InstalledAppFlow.from_client_secrets_file(str(self.client), SCOPES, autogenerate_code_verifier=True)
        self.credentials = flow.run_local_server(host='127.0.0.1', port=0, open_browser=True,
                                               timeout_seconds=180, access_type='offline', prompt='consent',
                                               authorization_prompt_message='Waiting for Google sign-in in your browser…',
                                               success_message='Calendar connected. You can close this tab.')
        self.save_token()
        self.service = None
        self.api()
        return True

    def api(self):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google.auth.exceptions import RefreshError
        from googleapiclient.discovery import build
        import httplib2
        import google_auth_httplib2
        if self.credentials is None:
            if not self.token.exists():
                raise Reconnect('Google Calendar is not connected yet')
            try:
                self.credentials = Credentials.from_authorized_user_file(str(self.token), SCOPES)
            except (ValueError, OSError):
                raise Reconnect('Reconnect your Google account') from None
        if not self.credentials.valid:
            try:
                self.credentials.refresh(Request())
                self.save_token()
            except RefreshError:
                self.credentials = None
                self.service = None
                raise Reconnect('Access revoked or expired. Reconnect your Google account') from None
        if self.service is None:
            http = google_auth_httplib2.AuthorizedHttp(self.credentials, http=httplib2.Http(timeout=15))
            self.service = build('calendar', 'v3', http=http, cache_discovery=False)
        return self.service

    def execute(self, request):
        from googleapiclient.errors import HttpError
        try:
            result = request.execute(num_retries=1)
            self.save_token()
            return result
        except HttpError as error:
            status = error.resp.status
            if status == 401:
                self.credentials = None
                self.service = None
                raise Reconnect('Please sign in to Google again') from None
            messages = {403: 'Permission denied or Calendar API is disabled',
                        404: 'Event or calendar no longer exists',
                        412: 'Event changed elsewhere. Refresh the calendar and reopen it',
                        429: 'Google rate limit reached. Try again later'}
            raise CalendarError(messages.get(status, f'Google Calendar error ({status})')) from None

    def sync(self, year, month, selected):
        service = self.api()
        calendars, page = [], None
        while True:
            result = self.execute(service.calendarList().list(pageToken=page))
            calendars.extend(result.get('items', []))
            page = result.get('nextPageToken')
            if not page:
                break
        if selected is None:
            selected = [item['id'] for item in calendars if item.get('primary')]
        today = datetime.now(TZ)
        lo = min(datetime(year, month, 1, tzinfo=TZ), today.replace(hour=0, minute=0, second=0, microsecond=0))
        next_month = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=TZ)
        hi = max(next_month, today + timedelta(days=8))
        events = []
        for cal in calendars:
            if cal['id'] not in selected:
                continue
            page = None
            while True:
                result = self.execute(service.events().list(calendarId=cal['id'], timeMin=lo.isoformat(),
                                      timeMax=hi.isoformat(), singleEvents=True, orderBy='startTime',
                                      showDeleted=False, maxResults=250, pageToken=page, timeZone='Europe/Prague'))
                for event in result.get('items', []):
                    if event.get('status') == 'cancelled':
                        continue
                    event['_calendar'] = cal['id']
                    event['_color'] = cal.get('backgroundColor', '#73daca')
                    event['_writable'] = cal.get('accessRole') in ('owner', 'writer') and event.get('eventType', 'default') == 'default' and not event.get('locked', False)
                    events.append(event)
                page = result.get('nextPageToken')
                if not page:
                    break
        result = dict(calendars=calendars, selected=selected, events=events, fetched=datetime.now(TZ).timestamp(),
                      year=year, month=month)
        write_json(DATA / 'calendar-cache.json', result)
        return result

    def mutate(self, operation, calendar_id, body=None, event=None, operation_id=None):
        """Stable IDs survive uncertain insert responses and application restarts."""
        service = self.api()
        from googleapiclient.errors import HttpError
        events = service.events()
        if operation == 'insert':
            if self.pending:
                if self.pending['operation_id'] != operation_id:
                    raise CalendarError('A write is pending. Check it in calendar settings first')
                if {key: value for key, value in self.pending['body'].items() if key != 'id'} != body:
                    raise CalendarError('Fields changed after disconnection. Resolve the previous write in settings first')
                return self.resolve_pending(service, retry=True)
            identifier = uuid.uuid4().hex
            self.pending = dict(calendar=calendar_id, id=identifier, body=dict(body, id=identifier), operation_id=operation_id)
            write_json(DATA / 'pending-event.json', self.pending)
            return self.resolve_pending(service, retry=True)
        request = (events.patch(calendarId=calendar_id, eventId=event['id'], body=body)
                   if operation == 'patch' else events.delete(calendarId=calendar_id, eventId=event['id']))
        if event.get('etag'):
            request.headers['If-Match'] = event['etag']
        if operation == 'delete':
            try:
                result = request.execute(num_retries=1)
                self.save_token()
                return result
            except HttpError as error:
                if error.resp.status in (404, 410):
                    return None
                raise CalendarError('Could not delete the event. Refresh the calendar and try again') from None
        return self.execute(request)

    def resolve_pending(self, service, retry=False):
        from googleapiclient.errors import HttpError
        pending = self.pending
        try:
            result = service.events().get(calendarId=pending['calendar'], eventId=pending['id']).execute(num_retries=1)
        except HttpError as error:
            if error.resp.status != 404:
                raise CalendarError('Could not check the previous write. Try again later') from None
            if not retry:
                raise CalendarError('A write is pending. Click "Check and finish previous write" in settings') from None
            try:
                result = service.events().insert(calendarId=pending['calendar'], body=pending['body']).execute(num_retries=0)
            except HttpError as error:
                if error.resp.status == 409:
                    result = self.execute(service.events().get(calendarId=pending['calendar'], eventId=pending['id']))
                elif 400 <= error.resp.status < 500 and error.resp.status not in (408, 429):
                    self.clear_pending()
                    raise CalendarError('Google rejected the event. Check the calendar and fields') from None
                else:
                    raise CalendarError('No response from Google. Retry saving; the event ID prevents duplicates') from None
            except (OSError, socket.timeout):
                raise CalendarError('Connection lost. Retry saving; the event ID prevents duplicates') from None
        self.clear_pending()
        self.save_token()
        return result

    def clear_pending(self):
        self.pending = None
        (DATA / 'pending-event.json').unlink(missing_ok=True)
