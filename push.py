import os, json, time, datetime
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request

SA = json.loads(os.environ["FIREBASE_SA"])
PROJECT = SA["project_id"]
CREDS = service_account.Credentials.from_service_account_info(
    SA,
    scopes=[
        "https://www.googleapis.com/auth/datastore",
        "https://www.googleapis.com/auth/firebase.messaging",
    ],
)
CREDS.refresh(Request())
H = {"Authorization": "Bearer " + CREDS.token}
FS = "https://firestore.googleapis.com/v1/projects/%s/databases/(default)/documents/devices" % PROJECT
FCM = "https://fcm.googleapis.com/v1/projects/%s/messages:send" % PROJECT

REPEAT_MIN = 28          # تذكير القراءة يتكرر كل نصف ساعة تقريباً
REMIND_END_MIN = 23 * 60 + 30   # آخر وقت للتذكير بالتوقيت المحلي
PRAY_WINDOW_SEC = 45 * 60       # لا نرسل إشعار صلاة متأخراً أكثر من 45 دقيقة
PN_AR = {"fajr": "الفجر", "dhuhr": "الظهر", "maghrib": "المغرب"}
PN_EN = {"fajr": "Fajr", "dhuhr": "Dhuhr", "maghrib": "Maghrib"}


def val(v):
    """تحويل قيمة Firestore REST إلى قيمة بايثون."""
    if not isinstance(v, dict):
        return None
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "booleanValue" in v:
        return v["booleanValue"]
    if "arrayValue" in v:
        return [val(x) for x in v["arrayValue"].get("values", [])]
    return None


def fields(doc):
    return {k: val(v) for k, v in doc.get("fields", {}).items()}


def send(token, title, body, lang):
    msg = {
        "message": {
            "token": token,
            "data": {"title": title, "body": body, "lang": lang or "ar"},
            "webpush": {"headers": {"Urgency": "high", "TTL": "3600"}},
        }
    }
    r = requests.post(FCM, headers=dict(H, **{"Content-Type": "application/json"}), json=msg, timeout=30)
    if r.status_code == 200:
        return "ok"
    t = r.text
    if any(x in t for x in ("UNREGISTERED", "INVALID_ARGUMENT", "NOT_FOUND")):
        return "dead"
    print("FCM error", r.status_code, t[:200])
    return "err"


def patch(doc_name, data):
    mask = "&".join("updateMask.fieldPaths=" + k for k in data)
    body = {"fields": {}}
    for k, v in data.items():
        if isinstance(v, str):
            body["fields"][k] = {"stringValue": v}
        elif isinstance(v, int):
            body["fields"][k] = {"integerValue": str(v)}
        elif isinstance(v, list):
            body["fields"][k] = {"arrayValue": {"values": [{"stringValue": x} for x in v]}}
    requests.patch("https://firestore.googleapis.com/v1/" + doc_name + "?" + mask, headers=H, json=body, timeout=30)


def process(f, now):
    """يرجع (قائمة رسائل [(العنوان, النص, lang)], حقول للتحديث)."""
    out, upd = [], {}
    tz = f.get("tz")
    tz = 180 if tz is None else int(tz)
    local = datetime.datetime.utcfromtimestamp(now + tz * 60)
    today = local.strftime("%Y-%m-%d")
    lang = f.get("lang") or "ar"
    title = f.get("title") or "🕌 ختمة"
    sent_ts = int(f.get("sentTs") or 0)

    # 1) تذكير القراءة: يتكرر كل نصف ساعة حتى يُكمل ورد اليوم
    if f.get("on") and f.get("doneDate") != today:
        try:
            hh, mm = (f.get("time") or "20:00").split(":")
            start = int(hh) * 60 + int(mm)
        except Exception:
            start = 20 * 60
        cur = local.hour * 60 + local.minute
        if start <= cur <= REMIND_END_MIN and now - sent_ts >= REPEAT_MIN * 60:
            body = f.get("msg") or ("هيّا أكمل قراءة القرآن 🌙" if lang != "en" else "Come on, continue reading the Quran 🌙")
            out.append((title, body, lang))
            upd["sentTs"] = now
            upd["sentDate"] = today

    # 2) إشعار وقت الصلاة (أوقات محسوبة من التطبيق نفسه)
    done = list(f.get("prayDone") or [])
    for item in f.get("pray") or []:
        try:
            ts, k = item.split("|")
            ts = int(ts)
        except Exception:
            continue
        if item in done:
            continue
        if ts <= now <= ts + PRAY_WINDOW_SEC:
            names = PN_EN if lang == "en" else PN_AR
            city = f.get("pcity") or ""
            if lang == "en":
                body = "It is time for %s adhan" % names.get(k, k) + (" - " + city if city else "")
            else:
                body = "حان الآن وقت أذان %s" % names.get(k, k) + (" - " + city if city else "")
            out.append((title, "🕌 " + body, lang))
            done.append(item)
    if done != list(f.get("prayDone") or []):
        upd["prayDone"] = done[-20:]
    return out, upd


def load_devices():
    devs = {}
    page = ""
    while True:
        url = FS + "?pageSize=300" + (("&pageToken=" + page) if page else "")
        j = requests.get(url, headers=H, timeout=30).json()
        for doc in j.get("documents", []):
            devs[doc["name"]] = fields(doc)
        page = j.get("nextPageToken", "")
        if not page:
            break
    return devs


# يعمل التشغيل الواحد حوالي 55 دقيقة ويفحص كل 20 ثانية، فيصل الإشعار في وقته تقريباً
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "3300"))
POLL_SECONDS = 20
RELOAD_SECONDS = 600


def main():
    start = time.time()
    sent = 0
    devs = load_devices()
    last_load = time.time()
    while True:
        now = int(time.time())
        for name in list(devs.keys()):
            f = devs[name]
            try:
                token = f.get("token")
                if not token:
                    continue
                msgs, upd = process(f, now)
                if not msgs and not upd:
                    continue
                # نسجّل الإرسال أولاً حتى لا يتكرر إن تداخل تشغيلان
                f.update(upd)
                if upd:
                    patch(name, upd)
                dead = False
                for (t, b, lg) in msgs:
                    res = send(token, t, b, lg)
                    if res == "ok":
                        sent += 1
                    elif res == "dead":
                        dead = True
                        break
                if dead:
                    requests.delete("https://firestore.googleapis.com/v1/" + name, headers=H, timeout=30)
                    devs.pop(name, None)
            except Exception as e:
                print("device error", e)
        if time.time() - start > RUN_SECONDS:
            break
        time.sleep(POLL_SECONDS)
        if time.time() - last_load > RELOAD_SECONDS:
            try:
                devs = load_devices()
                last_load = time.time()
                CREDS.refresh(Request())
                H["Authorization"] = "Bearer " + CREDS.token
            except Exception as e:
                print("reload error", e)
    print("sent", sent)


if __name__ == "__main__":
    main()
