import os, json, datetime, requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request

sa = json.loads(os.environ['FIREBASE_SA'])
creds = service_account.Credentials.from_service_account_info(
    sa, scopes=['https://www.googleapis.com/auth/datastore', 'https://www.googleapis.com/auth/firebase.messaging'])
creds.refresh(Request())
H = {'Authorization': 'Bearer ' + creds.token}
P = sa['project_id']
BASE = f'https://firestore.googleapis.com/v1/projects/{P}/databases/(default)/documents'
WINDOW_MIN = 180  # لا نرسل بعد مرور 3 ساعات على وقت التذكير

def val(f):
    for k, v in f.items():
        if k == 'integerValue': return int(v)
        return v
    return None

def devices():
    tok = None
    while True:
        r = requests.get(BASE + '/devices', headers=H, params={'pageSize': 300, **({'pageToken': tok} if tok else {})})
        r.raise_for_status()
        j = r.json()
        for d in j.get('documents', []):
            yield d['name'].split('/')[-1], {k: val(v) for k, v in d.get('fields', {}).items()}
        tok = j.get('nextPageToken')
        if not tok: break

now = datetime.datetime.now(datetime.timezone.utc)
sent = 0
for did, d in devices():
    try:
        if not d.get('on') or not d.get('token'): continue
        local = now + datetime.timedelta(minutes=int(d.get('tz', 180)))
        today = local.strftime('%Y-%m-%d')
        if d.get('sentDate') == today or d.get('doneDate') == today: continue
        h, m = map(int, str(d.get('time', '20:00')).split(':'))
        diff = local.hour * 60 + local.minute - (h * 60 + m)
        if diff < 0 or diff > WINDOW_MIN: continue
        body = d.get('msg') or 'ختمة'
        r = requests.post(f'https://fcm.googleapis.com/v1/projects/{P}/messages:send', headers=H, json={'message': {
            'token': d['token'],
            'data': {'title': d.get('title') or 'ختمة', 'body': body, 'lang': d.get('lang') or 'ar'},
            'webpush': {'headers': {'Urgency': 'high', 'TTL': '3600'}}}})
        if r.status_code == 200:
            requests.patch(f'{BASE}/devices/{did}', headers=H, params={'updateMask.fieldPaths': 'sentDate'},
                           json={'fields': {'sentDate': {'stringValue': today}}})
            sent += 1
        elif r.status_code in (400, 404) and ('UNREGISTERED' in r.text or 'INVALID_ARGUMENT' in r.text or 'NOT_FOUND' in r.text):
            requests.delete(f'{BASE}/devices/{did}', headers=H)
        else:
            print('FCM error', did, r.status_code, r.text[:200])
    except Exception as e:
        print('device error', did, e)
print('sent', sent)
