#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyDM 2.0 - مدير تحميل شبيه بـ IDM
متوافق مع Python 3.8+ (ويندوز 7 / 8 / 8.1 / 10 / 11 + لينكس + ماك)
بدون أي مكتبات خارجية.

المميزات:
  * تحميل متعدد الاتصالات + استئناف (حتى بعد إغلاق البرنامج)
  * تحديد السرعة (عام + لكل ملف) يتغير فورياً أثناء التحميل
  * طابور تحميل (عدد التحميلات المتزامنة) + جدولة + إجراء بعد الانتهاء
  * تصنيف تلقائي للملفات في مجلدات (فيديو، موسيقى، ...)
  * تكامل مع المتصفحات (Chrome / Edge / Firefox / Brave / Opera) عبر الإضافة المرفقة
  * مراقبة الحافظة، إضافة عدة روابط، إعادة المحاولة التلقائية
"""
import json
import mimetypes
import os
import queue
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog
except ImportError:  # يسمح باستخدام المحرك بدون واجهة
    tk = None

VERSION = "2.0"
UA = "Mozilla/5.0 (Windows NT 6.3; Win64; x64) PyDM/" + VERSION
CHUNK = 64 * 1024
TMP_EXT = ".pydm_tmp"

# ---------------------------------------------------------------- الحالات
QUEUED = "في الانتظار"
CONNECTING = "يتصل..."
DOWNLOADING = "جارٍ التحميل"
RETRYING = "إعادة المحاولة..."
PAUSED = "متوقف مؤقتاً"
DONE = "مكتمل"
ERROR = "خطأ"
CANCELLED = "ملغي"
ACTIVE = (CONNECTING, DOWNLOADING, RETRYING)

CATEGORIES = {
    "Videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp", ".ts"},
    "Music": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".epub", ".odt", ".csv", ".rtf"},
    "Compressed": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".cab"},
    "Programs": {".exe", ".msi", ".apk", ".dmg", ".deb", ".rpm", ".bat", ".appx"},
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".tiff", ".ico"},
}
DOWNLOADABLE_EXT = set().union(*CATEGORIES.values())

DEFAULTS = {
    "folder": os.path.join(os.path.expanduser("~"), "Downloads"),
    "threads": 8,
    "max_active": 3,
    "speed_limit_kb": 0,
    "auto_categories": True,
    "auto_retry": True,
    "auto_resume": True,
    "sound": True,
    "clipboard_monitor": False,
    "server_enabled": True,
    "server_port": 6800,
    "after_all": "none",      # none / close / shutdown
    "insecure_ssl": False,
}


# ---------------------------------------------------------------- أدوات عامة
def data_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, "PyDM")
    os.makedirs(d, exist_ok=True)
    return d


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir():
    """مجلد الملفات المضمّنة داخل EXE (أو مجلد البرنامج عند التشغيل من المصدر)."""
    return getattr(sys, "_MEIPASS", None) or app_dir()


def _is_windows():
    return os.name == "nt"


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "PyDM"


def autostart_command():
    if getattr(sys, "frozen", False):
        return '"%s" --minimized' % sys.executable
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return '"%s" "%s" --minimized' % (exe, os.path.abspath(__file__))


def get_autostart():
    if not _is_windows():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, RUN_NAME)
        return bool(val)
    except Exception:
        return False


def set_autostart(enabled):
    """يضيف/يحذف PyDM من برامج بدء تشغيل ويندوز (للمستخدم الحالي، بدون صلاحيات مدير)."""
    if not _is_windows():
        return False
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


_MUTEX = None


def single_instance():
    """يمنع تشغيل نسختين (مهم عند التشغيل التلقائي مع ويندوز). على غير ويندوز يعيد True."""
    global _MUTEX
    if not _is_windows():
        return True
    try:
        import ctypes
        _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\PyDM_SingleInstance_Mutex")
        return ctypes.windll.kernel32.GetLastError() != 183   # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def export_extension(kind, dest_parent):
    """ينسخ مجلد الإضافة (chrome أو firefox) من داخل البرنامج إلى مجلد يختاره المستخدم."""
    src = os.path.join(resource_dir(), "extension_" + kind)
    if not os.path.isdir(src):
        src = os.path.join(app_dir(), "extension_" + kind)
    if not os.path.isdir(src):
        raise IOError("مجلد الإضافة غير موجود")
    dst = os.path.join(dest_parent, "PyDM_extension_" + kind)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Config(dict):
    def __init__(self, path=None):
        super().__init__(DEFAULTS)
        self.path = path
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.update({k: v for k, v in data.items() if k in DEFAULTS})
            except Exception:
                pass

    def save(self):
        if self.path:
            try:
                write_json(self.path, dict(self))
            except Exception:
                pass


CFG = Config(None)
EVENTS = queue.Queue()   # أحداث من الخيوط الخلفية إلى الواجهة


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.0f B" % n if unit == "B" else "%.2f %s" % (n, unit)
        n /= 1024.0


def fmt_eta(sec):
    if sec is None or sec < 0 or sec > 99 * 3600:
        return ""
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%02d:%02d" % (m, s)


def fmt_limit(kb):
    if not kb:
        return "بلا حدود"
    if kb >= 1024 and kb % 1024 == 0:
        return "%d MB/s" % (kb // 1024)
    return "%d KB/s" % kb


def parse_speed(text):
    """يحوّل نصاً مثل '500' أو '2MB' أو 'بلا حدود' إلى KB/s (0 = بلا حدود). None إذا غير صالح."""
    t = (text or "").strip().lower()
    if not t or t.startswith(("بلا", "غير", "no", "unl")) or t in ("0", "∞"):
        return 0
    m = re.match(r"^([\d.]+)\s*([a-zء-ي]*)", t)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    if unit.startswith(("m", "م")):
        val *= 1024
    elif unit.startswith("g"):
        val *= 1024 * 1024
    return max(0, int(val))


def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:180] or "download"


def name_from_response(url, headers):
    cd = headers.get("Content-Disposition", "") or ""
    m = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)?'[^']*'([^;]+)", cd)
    if m:
        return safe_name(urllib.parse.unquote(m.group(1).strip().strip('"')))
    m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
    if m:
        return safe_name(urllib.parse.unquote(m.group(1).strip()))
    path = urllib.parse.urlparse(url).path
    name = safe_name(urllib.parse.unquote(os.path.basename(path)))
    if not os.path.splitext(name)[1]:
        ct = (headers.get("Content-Type", "") or "").split(";")[0].strip()
        ext = mimetypes.guess_extension(ct) if ct and ct != "text/html" else None
        if ext:
            name += ext
    return name


def category_for(name):
    ext = os.path.splitext(name)[1].lower()
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return "Other"


# ---------------------------------------------------------------- الشبكة
class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """لا يرسل الكوكيز/التفويض إلى موقع آخر عند إعادة التوجيه."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            a = urllib.parse.urlparse(newurl).netloc.lower()
            b = urllib.parse.urlparse(req.full_url).netloc.lower()
            if a != b:
                for h in ("Cookie", "Authorization"):
                    new.headers.pop(h, None)
                    new.unredirected_hdrs.pop(h, None)
        return new


_OPENER = None


def configure_net(insecure=False):
    global _OPENER
    ctx = ssl.create_default_context()
    try:  # شهادات حديثة (مفيد لويندوز 8.1 القديم) إن توفرت certifi
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    _OPENER = urllib.request.build_opener(SafeRedirect(), urllib.request.HTTPSHandler(context=ctx))


def net_open(req, timeout=20):
    if _OPENER is None:
        configure_net(CFG.get("insecure_ssl", False))
    return _OPENER.open(req, timeout=timeout)


class RateLimiter:
    """محدد سرعة بنظام الساعة الافتراضية (يعمل بين عدة خيوط)."""

    def __init__(self, rate=0):
        self.rate = int(rate)  # بايت/ثانية، 0 = بلا حدود
        self._lock = threading.Lock()
        self._t = time.monotonic()

    def set_rate(self, rate):
        with self._lock:
            self.rate = max(0, int(rate))
            self._t = time.monotonic()

    def consume(self, n, stop):
        with self._lock:
            rate = self.rate
            if rate <= 0:
                return
            now = time.monotonic()
            if self._t < now - 1.0:      # سماح بدفعة لا تتجاوز ثانية
                self._t = now - 1.0
            self._t += n / float(rate)
            wait = self._t - now
        while wait > 0 and not stop.is_set():
            d = min(wait, 0.2)
            time.sleep(d)
            wait -= d


GLOBAL_LIMITER = RateLimiter(0)


class RangeError(Exception):
    pass


class FatalError(Exception):
    """خطأ لا فائدة من إعادة المحاولة معه (مثل امتلاء القرص)."""


def _fatal_http(ex):
    return isinstance(ex, urllib.error.HTTPError) and 400 <= ex.code < 500 and ex.code not in (408, 429)


# ---------------------------------------------------------------- المهمة
class Task:
    def __init__(self, url, folder, nthreads=8, referer="", cookies="", ua="",
                 name_hint="", limit_kb=0, tid=None):
        self.id = tid or uuid.uuid4().hex[:10]
        self.url = url
        self.folder = folder
        self.nthreads = nthreads
        self.referer = referer
        self.cookies = cookies
        self.ua = ua
        self.name_hint = name_hint
        self.cookie_host = urllib.parse.urlparse(url).netloc.lower()
        self.name = name_hint or "..."
        self.path = None
        self.total = 0
        self.done = 0
        self.speed = 0.0
        self.resumable = False
        self.probed = False
        self.status = QUEUED
        self.error = ""
        self.n_parts = 0
        self.seg_done = []
        self.limit_kb = 0
        self.limiter = RateLimiter(0)
        self.set_limit(limit_kb)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self._thread = None
        self._tick = (time.time(), 0)

    # ----- إعدادات
    def set_limit(self, kb):
        self.limit_kb = max(0, int(kb))
        self.limiter.set_rate(self.limit_kb * 1024)

    def _headers(self, url, extra=None):
        h = {"User-Agent": self.ua or UA, "Accept": "*/*", "Accept-Encoding": "identity"}
        if self.referer:
            h["Referer"] = self.referer
        if self.cookies and urllib.parse.urlparse(url).netloc.lower() == self.cookie_host:
            h["Cookie"] = self.cookies
        if extra:
            h.update(extra)
        return h

    def _throttle(self, n, stop):
        GLOBAL_LIMITER.consume(n, stop)
        self.limiter.consume(n, stop)

    # ----- التحكم
    def start(self):
        if self.status in ACTIVE:
            return
        stop = threading.Event()
        self.stop = stop
        self.error = ""
        self.status = CONNECTING
        prev = self._thread
        t = threading.Thread(target=self._run, args=(stop, prev), daemon=True)
        self._thread = t
        t.start()

    def pause(self):
        if self.status in ACTIVE:
            self.stop.set()
            self.status = PAUSED
            self.speed = 0

    def cancel(self):
        self.stop.set()
        self.status = CANCELLED
        self.speed = 0
        th, path = self._thread, self.path

        def cleanup():
            if th is not None and th.is_alive():
                th.join()
            if path:
                try:
                    os.remove(path + TMP_EXT)
                except OSError:
                    pass
        threading.Thread(target=cleanup, daemon=True).start()

    def requeue(self, reset=False):
        """يعيد المهمة إلى الطابور. reset=True يبدأ التحميل من الصفر."""
        if self.status in ACTIVE:
            return
        if reset:
            if self.path:
                for p in (self.path + TMP_EXT,):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            self.probed = False
            self.n_parts = 0
            self.seg_done = []
            self.done = 0
            self.path = None
        self.error = ""
        self.status = QUEUED

    def update_speed(self):
        now = time.time()
        t0, d0 = self._tick
        if now - t0 >= 0.5:
            inst = max(0.0, (self.done - d0) / (now - t0))
            self.speed = inst if self.speed == 0 else 0.5 * self.speed + 0.5 * inst
            self._tick = (now, self.done)
        if self.status != DOWNLOADING:
            self.speed = 0.0

    def eta(self):
        if self.status == DOWNLOADING and self.speed > 1 and self.total:
            return fmt_eta((self.total - self.done) / self.speed)
        return ""

    # ----- التنفيذ
    def _set(self, stop, status):
        if not stop.is_set():
            self.status = status

    def _run(self, stop, prev):
        if prev is not None:
            prev.join()
        attempts = 0
        max_attempts = 4 if CFG.get("auto_retry", True) else 1
        while not stop.is_set():
            try:
                if not self.probed:
                    self._probe(stop)
                if stop.is_set():
                    return
                self._set(stop, DOWNLOADING)
                self._tick = (time.time(), self.done)
                if self.resumable and self.total > 0:
                    self._download_multi(stop)
                else:
                    self._download_single(stop)
                if stop.is_set():
                    return
                self.done = self.total
                self.speed = 0.0
                self.error = ""
                self.cookies = ""
                self.status = DONE
                EVENTS.put(("done", self))
                return
            except RangeError:
                self.resumable = False       # الخادم لا يدعم Range: ننتقل لتحميل مفرد
                self.n_parts = 0
                self.seg_done = []
                continue
            except Exception as e:  # noqa
                if stop.is_set():
                    return
                attempts += 1
                self.error = str(e) or e.__class__.__name__
                if attempts >= max_attempts or _fatal_http(e) or isinstance(e, FatalError):
                    self.status = ERROR
                    return
                self._set(stop, RETRYING)
                for _ in range(5):
                    if stop.is_set():
                        return
                    time.sleep(1)

    def _probe(self, stop):
        req = Request(self.url, headers=self._headers(self.url, {"Range": "bytes=0-0"}))
        with net_open(req, 20) as r:
            final = r.geturl()
            hdr = r.headers
            status = getattr(r, "status", None) or r.getcode()
            total, resumable = 0, False
            if status == 206:
                m = re.search(r"/(\d+)\s*$", hdr.get("Content-Range", "") or "")
                if m:
                    total, resumable = int(m.group(1)), True
            else:
                total = int(hdr.get("Content-Length") or 0)
                resumable = (hdr.get("Accept-Ranges", "") or "").lower() == "bytes" and total > 0
        name = self.name_hint or name_from_response(final, hdr)
        name = safe_name(name)
        folder = self.folder
        if CFG.get("auto_categories", True):
            folder = os.path.join(folder, category_for(name))
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, name)
        root, ext = os.path.splitext(base)
        i = 1
        while os.path.exists(base) or os.path.exists(base + TMP_EXT):
            base = "%s (%d)%s" % (root, i, ext)
            i += 1
        self.url = final
        self.total, self.resumable = total, resumable
        self.path = base
        self.name = os.path.basename(base)
        self.probed = True

    def _check_space(self, need):
        free = shutil.disk_usage(os.path.dirname(self.path)).free
        if need and free < need + 10 * 1024 * 1024:
            raise FatalError("مساحة القرص غير كافية")

    def _download_multi(self, stop):
        total = self.total
        n = self.n_parts
        if not n:
            n = 1 if total < 2 * 1024 * 1024 else max(1, min(int(self.nthreads), 32))
            self.n_parts = n
            self.seg_done = [0] * n
        if len(self.seg_done) != n:
            self.seg_done = [0] * n
        step = total // n
        ranges = [(i * step, total - 1 if i == n - 1 else (i + 1) * step - 1) for i in range(n)]
        tmp = self.path + TMP_EXT
        if not os.path.exists(tmp) or os.path.getsize(tmp) != total:
            self.seg_done = [0] * n
            self._check_space(total)
            with open(tmp, "wb") as f:
                f.truncate(total)
        for i, (s, e) in enumerate(ranges):
            self.seg_done[i] = min(self.seg_done[i], e - s + 1)
        self.done = sum(self.seg_done)
        self._tick = (time.time(), self.done)
        errors = []
        halt = threading.Event()   # يوقف بقية الأجزاء عند فشل جزء (دون لمس stop)

        def stopped():
            return stop.is_set() or halt.is_set()

        def worker(i):
            s, e = ranges[i]
            length = e - s + 1
            last = None
            for _ in range(6):
                if stopped():
                    return
                if self.seg_done[i] >= length:
                    return
                try:
                    pos = s + self.seg_done[i]
                    req = Request(self.url, headers=self._headers(self.url, {"Range": "bytes=%d-%d" % (pos, e)}))
                    with net_open(req, 20) as r, open(tmp, "r+b") as f:
                        if (getattr(r, "status", None) or r.getcode()) != 206:
                            raise RangeError("no range")
                        f.seek(pos)
                        while not stopped():
                            want = min(CHUNK, length - self.seg_done[i])
                            if want <= 0:
                                break
                            b = r.read(want)
                            if not b:
                                break
                            f.write(b)
                            self.seg_done[i] += len(b)
                            with self.lock:
                                self.done += len(b)
                            self._throttle(len(b), stop)
                except RangeError as ex:
                    errors.append(ex)
                    halt.set()
                    return
                except Exception as ex:  # noqa
                    last = ex
                    if _fatal_http(ex):
                        errors.append(ex)
                        halt.set()
                        return
                    if stopped():
                        return
                    time.sleep(2)
                    continue
                if stopped() or self.seg_done[i] >= length:
                    return
            errors.append(last or IOError("فشل تحميل جزء من الملف"))
            halt.set()

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if stop.is_set():
            return
        if errors:
            raise errors[0]
        if sum(self.seg_done) < total:
            raise IOError("التحميل غير مكتمل")
        os.replace(tmp, self.path)

    def _download_single(self, stop):
        tmp = self.path + TMP_EXT
        self.done = 0
        self.seg_done = []
        if self.total:
            self._check_space(self.total)
        req = Request(self.url, headers=self._headers(self.url))
        with net_open(req, 20) as r, open(tmp, "wb") as f:
            if not self.total:
                self.total = int(r.headers.get("Content-Length") or 0)
            while not stop.is_set():
                b = r.read(CHUNK)
                if not b:
                    break
                f.write(b)
                self.done += len(b)
                self._throttle(len(b), stop)
        if stop.is_set():
            return
        if self.total and self.done < self.total:
            raise IOError("انقطع الاتصال قبل اكتمال الملف")
        os.replace(tmp, self.path)
        self.total = self.done

    # ----- الحفظ
    def to_dict(self):
        st = QUEUED if (self.status in ACTIVE or self.status == QUEUED) else self.status
        return {
            "id": self.id, "url": self.url, "folder": self.folder, "nthreads": self.nthreads,
            "referer": self.referer, "ua": self.ua, "name": self.name, "name_hint": self.name_hint,
            "path": self.path, "total": self.total, "done": self.done, "resumable": self.resumable,
            "probed": self.probed, "status": st, "n_parts": self.n_parts,
            "seg_done": list(self.seg_done), "limit_kb": self.limit_kb,
            "cookie_host": self.cookie_host, "error": self.error,
            "cookies": "" if st == DONE else self.cookies,
        }

    @classmethod
    def from_dict(cls, d):
        t = cls(d["url"], d.get("folder") or CFG["folder"], d.get("nthreads", 8), d.get("referer", ""),
                d.get("cookies", ""), d.get("ua", ""), d.get("name_hint", ""), d.get("limit_kb", 0), d.get("id"))
        t.name = d.get("name", t.name)
        t.path = d.get("path")
        t.total = d.get("total", 0)
        t.done = d.get("done", 0)
        t.resumable = d.get("resumable", False)
        t.probed = d.get("probed", False)
        t.n_parts = d.get("n_parts", 0)
        t.seg_done = d.get("seg_done", [])
        t.cookie_host = d.get("cookie_host", t.cookie_host)
        t.error = d.get("error", "")
        st = d.get("status", PAUSED)
        if st == DONE:
            t.status = DONE
        elif st == QUEUED:
            t.status = QUEUED if CFG.get("auto_resume", True) else PAUSED
        else:
            t.status = PAUSED
        return t


# ---------------------------------------------------------------- خادم التكامل مع المتصفح
class IntegrationServer:
    """خادم محلي (127.0.0.1) تستقبل منه إضافة المتصفح الروابط."""

    def __init__(self, port):
        self.port = port
        self.httpd = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "PyDM"

            def log_message(self, *a):
                pass

            def _ok(self):
                host = (self.headers.get("Host") or "").lower()
                if host not in ("127.0.0.1:%d" % outer.port, "localhost:%d" % outer.port):
                    return False          # حماية من DNS rebinding
                origin = self.headers.get("Origin")
                if origin and not origin.startswith(("chrome-extension://", "moz-extension://",
                                                     "safari-web-extension://")):
                    return False          # صفحات الويب العادية مرفوضة
                return True

            def _send(self, code, obj=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                origin = self.headers.get("Origin")
                if origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Access-Control-Allow-Headers", "Content-Type")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    self.send_header("Access-Control-Max-Age", "600")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_OPTIONS(self):
                self._send(204 if self._ok() else 403)

            def do_GET(self):
                if not self._ok():
                    return self._send(403, {"error": "forbidden"})
                if self.path.split("?")[0] == "/ping":
                    return self._send(200, {"app": "PyDM", "version": VERSION})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._ok():
                    return self._send(403, {"error": "forbidden"})
                if self.path.split("?")[0] != "/add":
                    return self._send(404, {"error": "not found"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if n <= 0 or n > 2 * 1024 * 1024:
                        raise ValueError("size")
                    data = json.loads(self.rfile.read(n).decode("utf-8"))
                    url = str(data.get("url", "")).strip()
                    if not re.match(r"^https?://", url, re.I):
                        raise ValueError("url")
                except Exception:
                    return self._send(400, {"error": "bad request"})
                EVENTS.put(("add", {
                    "url": url,
                    "referer": str(data.get("referer", ""))[:2000],
                    "cookies": str(data.get("cookies", ""))[:60000],
                    "ua": str(data.get("userAgent", ""))[:500],
                    "filename": str(data.get("filename", ""))[:300],
                }))
                self._send(200, {"ok": True})

        class Srv(ThreadingHTTPServer):
            allow_reuse_address = (os.name != "nt")   # على ويندوز يسمح بسرقة المنفذ
            daemon_threads = True

        self.httpd = Srv(("127.0.0.1", port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None


# ---------------------------------------------------------------- الأيقونة
ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAABHUlEQVR42u2avQ2DMBCF6bNHZvAidMwQiVUYA4mWGZiB0rS0qR2flET5cXAOG+xD76RXgeF9xj7A56LwxKnqlFV5V7uTHvdTxdqwjWsrbWUSizzUHOMqE+MuEOUzX2Zo/FPlUs8bIVIuAC0IQLsmrBGmWmrvvz8FYWP/ey4IyTy/M1JsgKoZzFLQ8dgALQAAAAAAAAAAAAAAAAAAAABI/SMDgFiAph9NN0zBouskAThfejPNVxMS1J6uk2wI0c2HcV5lntoFmI87B2gocILOz24S/wsRyTxeZGyIyOa3WVp0ZajATJMG4DVDbWT+CcBaXt8j2KUmToEjIwC9qsQU49PBJ3aJSXyR7xBlVvGF7kNsNTjEZg/Pdpu95e3xG7hwkOo+HNXjAAAAAElFTkSuQmCC"


def beep():
    if not CFG.get("sound", True):
        return
    try:
        import winsound
        winsound.MessageBeep()
    except Exception:
        pass


def open_path(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def reveal_file(path):
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            open_path(os.path.dirname(path))
    except Exception:
        pass


# ---------------------------------------------------------------- الواجهة
class App(tk.Tk if tk else object):
    COLS = (("name", "الملف", 270), ("size", "الحجم", 80), ("progress", "التقدم", 140),
            ("speed", "السرعة", 85), ("eta", "المتبقي", 65), ("limit", "الحد", 75),
            ("status", "الحالة", 110))
    SPEED_PRESETS = ["بلا حدود", "128 KB/s", "256 KB/s", "512 KB/s", "1 MB/s", "2 MB/s", "5 MB/s", "10 MB/s"]
    AFTER = (("none", "لا شيء"), ("close", "إغلاق البرنامج"), ("shutdown", "إيقاف تشغيل الجهاز"))

    def __init__(self):
        super().__init__()
        self.title("PyDM %s - مدير التحميل" % VERSION)
        self.geometry("1020x540")
        self.minsize(820, 380)
        try:
            self.iconphoto(True, tk.PhotoImage(data=ICON_B64))
        except Exception:
            pass
        self.tasks = {}
        self.server = None
        self.schedule_at = None
        self._was_busy = False
        self._batch_done = 0
        self._last_clip = ""
        self._clip_busy = False
        self._dirty = False
        self._last_save = 0.0
        self._msg_until = 0.0
        self.msg_var = tk.StringVar()
        self.info_var = tk.StringVar()
        self.after_var = tk.StringVar(value=CFG["after_all"])
        self.folder_var = tk.StringVar(value=CFG["folder"])
        self.limit_var = tk.StringVar(value=fmt_limit(CFG["speed_limit_kb"]))

        self._build_menu()
        self._build_ui()
        self._load_tasks()
        GLOBAL_LIMITER.set_rate(CFG["speed_limit_kb"] * 1024)
        self._start_server()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(300, self._tick)
        self.after(1500, self._clip_tick)

    # ----- بناء الواجهة
    def _build_menu(self):
        mb = tk.Menu(self)
        f = tk.Menu(mb, tearoff=0)
        f.add_command(label="إضافة عدة روابط...", command=self.add_many_dialog)
        f.add_command(label="فتح مجلد التحميل", command=lambda: open_path(CFG["folder"]))
        f.add_separator()
        f.add_command(label="خروج", command=self.on_close)
        mb.add_cascade(label="ملف", menu=f)

        t = tk.Menu(mb, tearoff=0)
        t.add_command(label="استئناف المحدد", command=self.do_resume)
        t.add_command(label="إيقاف مؤقت للمحدد", command=self.do_pause)
        t.add_command(label="إلغاء المحدد", command=self.do_cancel)
        t.add_separator()
        t.add_command(label="استئناف الكل", command=self.resume_all)
        t.add_command(label="إيقاف الكل", command=self.pause_all)
        t.add_command(label="حذف المكتمل من القائمة", command=self.clear_done)
        mb.add_cascade(label="المهام", menu=t)

        tools = tk.Menu(mb, tearoff=0)
        tools.add_command(label="الإعدادات...", command=self.settings_dialog)
        tools.add_command(label="جدولة بدء الطابور...", command=self.schedule_dialog)
        tools.add_command(label="إلغاء الجدولة", command=self.cancel_schedule)
        tools.add_separator()
        sub = tk.Menu(tools, tearoff=0)
        for key, label in self.AFTER:
            sub.add_radiobutton(label=label, value=key, variable=self.after_var, command=self._after_changed)
        tools.add_cascade(label="بعد انتهاء كل التحميلات", menu=sub)
        mb.add_cascade(label="أدوات", menu=tools)

        h = tk.Menu(mb, tearoff=0)
        h.add_command(label="تثبيت إضافة المتصفح...", command=self.extension_help)
        h.add_command(label="حول", command=lambda: messagebox.showinfo(
            "PyDM", "PyDM %s\nمدير تحميل مفتوح المصدر بلغة Python" % VERSION))
        mb.add_cascade(label="مساعدة", menu=h)
        self.config(menu=mb)

        self.ctx = tk.Menu(self, tearoff=0)
        for label, cmd in (("استئناف", self.do_resume), ("إيقاف مؤقت", self.do_pause), ("إلغاء", self.do_cancel),
                           (None, None),
                           ("إعادة التحميل من البداية", self.do_redownload),
                           ("تحديد سرعة لهذا الملف...", self.do_task_limit),
                           (None, None),
                           ("فتح الملف", self.open_file), ("إظهار في المجلد", self.open_in_folder),
                           ("نسخ الرابط", self.copy_url),
                           (None, None),
                           ("حذف من القائمة", lambda: self.do_remove(False)),
                           ("حذف من القائمة ومن القرص", lambda: self.do_remove(True))):
            if label is None:
                self.ctx.add_separator()
            else:
                self.ctx.add_command(label=label, command=cmd)

    def _build_ui(self):
        top = ttk.Frame(self, padding=(8, 8, 8, 2))
        top.pack(fill="x")
        ttk.Label(top, text="الرابط:").pack(side="left")
        self.url = ttk.Entry(top)
        self.url.pack(side="left", fill="x", expand=True, padx=6)
        self.url.bind("<Return>", lambda e: self.add_from_entry())
        ttk.Button(top, text="لصق", command=self.paste).pack(side="left")
        ttk.Button(top, text="إضافة وتحميل", command=self.add_from_entry).pack(side="left", padx=4)
        ttk.Button(top, text="عدة روابط...", command=self.add_many_dialog).pack(side="left")

        opt = ttk.Frame(self, padding=(8, 2))
        opt.pack(fill="x")
        ttk.Label(opt, text="حفظ في:").pack(side="left")
        ttk.Entry(opt, textvariable=self.folder_var, state="readonly").pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(opt, text="تغيير...", command=self.pick_folder).pack(side="left")
        ttk.Label(opt, text="   حد السرعة العام:").pack(side="left")
        cb = ttk.Combobox(opt, textvariable=self.limit_var, values=self.SPEED_PRESETS, width=12)
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.apply_global_limit())
        cb.bind("<Return>", lambda e: self.apply_global_limit())
        ttk.Button(opt, text="تطبيق", command=self.apply_global_limit).pack(side="left")

        frame = ttk.Frame(self, padding=(8, 6))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in self.COLS], show="headings", selectmode="extended")
        for key, title, w in self.COLS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=w, anchor="w" if key == "name" else "center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("error", foreground="#b00020")
        self.tree.tag_configure("done", foreground="#1b7f3b")
        self.tree.tag_configure("paused", foreground="#8a6d00")
        self.tree.bind("<Button-3>", self._popup)
        self.tree.bind("<Double-1>", lambda e: self.open_file())
        self.tree.bind("<Delete>", lambda e: self.do_remove(False))

        bar = ttk.Frame(self, padding=(8, 0, 8, 4))
        bar.pack(fill="x")
        for text, cmd in (("استئناف", self.do_resume), ("إيقاف مؤقت", self.do_pause), ("إلغاء", self.do_cancel),
                          ("حذف", lambda: self.do_remove(False)), ("فتح الملف", self.open_file),
                          ("فتح المجلد", lambda: open_path(CFG["folder"]))):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=3)

        st = ttk.Frame(self, padding=(10, 2, 10, 6))
        st.pack(fill="x")
        ttk.Label(st, textvariable=self.info_var).pack(side="left")
        ttk.Label(st, textvariable=self.msg_var, foreground="#0b5cad").pack(side="right")

    # ----- الحفظ والتحميل
    def _load_tasks(self):
        path = os.path.join(data_dir(), "tasks.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            return
        for d in items:
            try:
                t = Task.from_dict(d)
            except Exception:
                continue
            self.tasks[t.id] = t
            self.tree.insert("", "end", t.id, values=self._row(t))

    def save_tasks(self):
        try:
            write_json(os.path.join(data_dir(), "tasks.json"),
                       [t.to_dict() for t in self.tasks.values() if t.status != CANCELLED])
        except Exception:
            pass
        self._dirty = False
        self._last_save = time.time()

    # ----- الخادم
    def _start_server(self):
        if self.server:
            self.server.stop()
            self.server = None
        if not CFG["server_enabled"]:
            return
        try:
            self.server = IntegrationServer(int(CFG["server_port"]))
        except Exception as e:
            self.flash("تعذّر تشغيل خادم المتصفح (المنفذ %s مشغول؟)" % CFG["server_port"])

    # ----- إضافة المهام
    def flash(self, text, secs=8):
        self.msg_var.set(text)
        self._msg_until = time.time() + secs

    def add_task(self, url, referer="", cookies="", ua="", name_hint=""):
        url = url.strip()
        if not re.match(r"^https?://", url, re.I):
            return None
        for t in self.tasks.values():
            if t.url == url and t.status not in (DONE, CANCELLED, ERROR):
                self.flash("الرابط موجود بالفعل في القائمة")
                return t
        t = Task(url, CFG["folder"], CFG["threads"], referer, cookies, ua, name_hint)
        self.tasks[t.id] = t
        self.tree.insert("", "end", t.id, values=self._row(t))
        self.tree.see(t.id)
        self._dirty = True
        return t

    def add_from_entry(self):
        text = self.url.get().strip()
        if not text:
            return
        if not re.match(r"^https?://", text, re.I):
            messagebox.showwarning("رابط غير صحيح", "اكتب رابطاً يبدأ بـ http:// أو https://")
            return
        self.add_task(text)
        self.url.delete(0, "end")

    def paste(self):
        try:
            self.url.delete(0, "end")
            self.url.insert(0, self.clipboard_get().strip())
        except tk.TclError:
            pass

    def add_many_dialog(self):
        win = tk.Toplevel(self)
        win.title("إضافة عدة روابط")
        win.geometry("560x340")
        win.transient(self)
        ttk.Label(win, text="ضع كل رابط في سطر مستقل:", padding=8).pack(anchor="w")
        txt = tk.Text(win, wrap="none")
        txt.pack(fill="both", expand=True, padx=8)
        try:
            txt.insert("1.0", self.clipboard_get())
        except tk.TclError:
            pass

        def ok():
            n = 0
            for line in txt.get("1.0", "end").splitlines():
                if self.add_task(line):
                    n += 1
            win.destroy()
            self.flash("تمت إضافة %d رابط" % n)
        ttk.Button(win, text="إضافة الكل", command=ok).pack(pady=8)

    def pick_folder(self):
        d = filedialog.askdirectory(initialdir=CFG["folder"])
        if d:
            CFG["folder"] = d
            self.folder_var.set(d)
            CFG.save()

    # ----- الأوامر
    def _sel(self):
        return [self.tasks[i] for i in self.tree.selection() if i in self.tasks]

    def do_resume(self):
        for t in self._sel():
            if t.status in (PAUSED, ERROR, CANCELLED):
                t.requeue(reset=(t.status == CANCELLED))
        self._dirty = True

    def do_pause(self):
        for t in self._sel():
            if t.status in ACTIVE:
                t.pause()
            elif t.status == QUEUED:
                t.status = PAUSED
        self._dirty = True

    def do_cancel(self):
        for t in self._sel():
            if t.status not in (DONE, CANCELLED):
                t.cancel()
        self._dirty = True

    def do_redownload(self):
        for t in self._sel():
            if t.status in ACTIVE:
                t.pause()
            t.requeue(reset=True)
        self._dirty = True

    def do_task_limit(self):
        sel = self._sel()
        if not sel:
            return
        s = simpledialog.askstring("حد السرعة للملف", "اكتب الحد (مثال: 300 أو 2MB ، 0 = بلا حدود):",
                                   initialvalue=str(sel[0].limit_kb), parent=self)
        if s is None:
            return
        kb = parse_speed(s)
        if kb is None:
            messagebox.showwarning("قيمة غير صحيحة", "اكتب رقماً مثل 500 أو 2MB")
            return
        for t in sel:
            t.set_limit(kb)
        self._dirty = True

    def do_remove(self, delete_file=False):
        sel = self.tree.selection()
        if delete_file and sel and not messagebox.askyesno("تأكيد", "سيتم حذف الملفات من القرص أيضاً. متابعة؟"):
            return
        for iid in sel:
            t = self.tasks.pop(iid, None)
            if t:
                if t.status not in (DONE, CANCELLED):
                    t.cancel()
                if delete_file and t.path:
                    try:
                        os.remove(t.path)
                    except OSError:
                        pass
            self.tree.delete(iid)
        self._dirty = True

    def clear_done(self):
        for iid in [i for i, t in self.tasks.items() if t.status in (DONE, CANCELLED)]:
            self.tasks.pop(iid)
            self.tree.delete(iid)
        self._dirty = True

    def pause_all(self):
        for t in self.tasks.values():
            if t.status in ACTIVE:
                t.pause()
            elif t.status == QUEUED:
                t.status = PAUSED
        self._dirty = True

    def resume_all(self):
        for t in self.tasks.values():
            if t.status in (PAUSED, ERROR):
                t.requeue()
        self._dirty = True

    def open_file(self):
        for t in self._sel():
            if t.status == DONE and t.path and os.path.exists(t.path):
                open_path(t.path)

    def open_in_folder(self):
        for t in self._sel():
            if t.path and os.path.exists(t.path):
                reveal_file(t.path)
            elif t.path:
                open_path(os.path.dirname(t.path))

    def copy_url(self):
        sel = self._sel()
        if sel:
            self.clipboard_clear()
            self.clipboard_append("\n".join(t.url for t in sel))

    def _popup(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.ctx.tk_popup(event.x_root, event.y_root)

    # ----- السرعة والجدولة
    def apply_global_limit(self):
        kb = parse_speed(self.limit_var.get())
        if kb is None:
            messagebox.showwarning("قيمة غير صحيحة", "اكتب رقماً مثل 500 (KB/s) أو 2MB أو اختر من القائمة")
            self.limit_var.set(fmt_limit(CFG["speed_limit_kb"]))
            return
        CFG["speed_limit_kb"] = kb
        GLOBAL_LIMITER.set_rate(kb * 1024)
        self.limit_var.set(fmt_limit(kb))
        CFG.save()
        self.flash("حد السرعة العام: " + fmt_limit(kb))

    def schedule_dialog(self):
        s = simpledialog.askstring("جدولة", "ابدأ الطابور عند الساعة (بصيغة HH:MM مثل 02:30):", parent=self)
        if not s:
            return
        m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", s)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            messagebox.showwarning("وقت غير صحيح", "اكتب الوقت بصيغة 24 ساعة مثل 02:30 أو 21:15")
            return
        now = datetime.now()
        at = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
        self.schedule_at = at

    def cancel_schedule(self):
        self.schedule_at = None
        self.flash("أُلغيت الجدولة")

    def _after_changed(self):
        CFG["after_all"] = self.after_var.get()
        CFG.save()

    # ----- حلقة التحديث
    def _row(self, t):
        pct = (t.done * 100.0 / t.total) if t.total else 0
        if t.total:
            n = int(pct // 10)
            prog = "%s %.0f%%" % ("█" * n + "░" * (10 - n), pct)
        else:
            prog = human(t.done) if t.done else ""
        return (t.name, human(t.total) if t.total else ("؟" if t.status != DONE else human(t.done)),
                prog, ("%s/s" % human(t.speed)) if t.speed else "", t.eta(),
                fmt_limit(t.limit_kb) if t.limit_kb else "", t.status)

    def _process_events(self):
        while True:
            try:
                kind, payload = EVENTS.get_nowait()
            except queue.Empty:
                break
            if kind == "done":
                self._batch_done += 1
                beep()
                self.flash("اكتمل: " + payload.name)
                self._dirty = True
            elif kind == "add":
                t = self.add_task(payload["url"], payload["referer"], payload["cookies"],
                                  payload["ua"], payload["filename"])
                if t:
                    self.flash("أُضيف من المتصفح: " + (t.name_hint or t.url[:60]))
                    try:
                        self.bell()
                    except Exception:
                        pass

    def _tick(self):
        try:
            self._process_events()
            now = datetime.now()
            if self.schedule_at and now >= self.schedule_at:
                self.schedule_at = None
                self.flash("بدأ تشغيل الطابور المجدول")
            if not self.schedule_at:
                active = sum(1 for t in self.tasks.values() if t.status in ACTIVE)
                for t in self.tasks.values():
                    if active >= max(1, int(CFG["max_active"])):
                        break
                    if t.status == QUEUED:
                        t.start()
                        active += 1
            total_speed = 0.0
            n_active = 0
            for iid, t in self.tasks.items():
                t.update_speed()
                total_speed += t.speed
                if t.status in ACTIVE:
                    n_active += 1
                tag = ""
                if t.status == ERROR:
                    tag = "error"
                elif t.status == DONE:
                    tag = "done"
                elif t.status == PAUSED:
                    tag = "paused"
                self.tree.item(iid, values=self._row(t), tags=(tag,) if tag else ())
            parts = ["نشطة: %d" % n_active, "السرعة: %s/s" % human(total_speed)]
            if CFG["speed_limit_kb"]:
                parts.append("الحد: " + fmt_limit(CFG["speed_limit_kb"]))
            if self.schedule_at:
                parts.append("الطابور مجدول للساعة " + self.schedule_at.strftime("%H:%M"))
            if CFG["server_enabled"] and self.server:
                parts.append("المتصفح: متصل على المنفذ %s" % CFG["server_port"])
            self.info_var.set("   |   ".join(parts))
            err = [t for t in self.tasks.values() if t.status == ERROR and t.error]
            if time.time() > self._msg_until:
                self.msg_var.set(("خطأ: " + err[-1].error[:80]) if err else "")

            busy = any(t.status in ACTIVE or t.status == QUEUED for t in self.tasks.values())
            if self._was_busy and not busy and self._batch_done:
                self._all_done()
            self._was_busy = busy

            if self._dirty and time.time() - self._last_save > 3:
                self.save_tasks()
        except Exception as e:  # لا نسمح لخطأ بإيقاف الحلقة
            self.msg_var.set("خطأ داخلي: %s" % e)
        self.after(500, self._tick)

    def _all_done(self):
        action = CFG.get("after_all", "none")
        self._batch_done = 0
        self.save_tasks()
        if action == "close":
            self.on_close(force=True)
        elif action == "shutdown":
            self._countdown()

    def _countdown(self):
        win = tk.Toplevel(self)
        win.title("PyDM")
        win.resizable(False, False)
        lbl = ttk.Label(win, padding=20, justify="center")
        lbl.pack()
        state = {"n": 30, "stop": False}

        def cancel():
            state["stop"] = True
            win.destroy()
        ttk.Button(win, text="إلغاء الإيقاف", command=cancel).pack(pady=(0, 14))

        def step():
            if state["stop"]:
                return
            if state["n"] <= 0:
                win.destroy()
                self._power_off()
                return
            lbl.config(text="اكتملت كل التحميلات.\nسيتم إيقاف تشغيل الجهاز خلال %d ثانية" % state["n"])
            state["n"] -= 1
            self.after(1000, step)
        step()

    def _power_off(self):
        self.save_tasks()
        try:
            if os.name == "nt":
                subprocess.Popen(["shutdown", "/s", "/t", "5"])
            elif sys.platform == "darwin":
                subprocess.Popen(["osascript", "-e", 'tell app "System Events" to shut down'])
            else:
                subprocess.Popen(["systemctl", "poweroff"])
        except Exception:
            pass

    # ----- مراقبة الحافظة
    def _clip_tick(self):
        try:
            if CFG["clipboard_monitor"] and not self._clip_busy:
                try:
                    text = self.clipboard_get().strip()
                except tk.TclError:
                    text = ""
                if text and text != self._last_clip and "\n" not in text and re.match(r"^https?://\S+$", text, re.I):
                    self._last_clip = text
                    path = urllib.parse.urlparse(text).path
                    if os.path.splitext(path)[1].lower() in DOWNLOADABLE_EXT:
                        self._clip_busy = True
                        try:
                            if messagebox.askyesno("PyDM", "تم نسخ رابط تحميل:\n\n%s\n\nهل تريد تحميله؟" % text[:200]):
                                self.add_task(text)
                        finally:
                            self._clip_busy = False
        except Exception:
            pass
        self.after(1500, self._clip_tick)

    # ----- الإعدادات
    def settings_dialog(self):
        win = tk.Toplevel(self)
        win.title("الإعدادات")
        win.transient(self)
        win.resizable(False, False)
        f = ttk.Frame(win, padding=14)
        f.pack(fill="both", expand=True)
        v = {
            "folder": tk.StringVar(value=CFG["folder"]),
            "threads": tk.IntVar(value=CFG["threads"]),
            "max_active": tk.IntVar(value=CFG["max_active"]),
            "limit": tk.StringVar(value=fmt_limit(CFG["speed_limit_kb"])),
            "auto_categories": tk.BooleanVar(value=CFG["auto_categories"]),
            "auto_retry": tk.BooleanVar(value=CFG["auto_retry"]),
            "auto_resume": tk.BooleanVar(value=CFG["auto_resume"]),
            "sound": tk.BooleanVar(value=CFG["sound"]),
            "clipboard_monitor": tk.BooleanVar(value=CFG["clipboard_monitor"]),
            "server_enabled": tk.BooleanVar(value=CFG["server_enabled"]),
            "server_port": tk.IntVar(value=CFG["server_port"]),
            "insecure_ssl": tk.BooleanVar(value=CFG["insecure_ssl"]),
            "autostart": tk.BooleanVar(value=get_autostart()),
        }
        r = 0
        ttk.Label(f, text="مجلد التحميل:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Entry(f, textvariable=v["folder"], width=38).grid(row=r, column=1, pady=3)
        ttk.Button(f, text="...", width=3, command=lambda: v["folder"].set(
            filedialog.askdirectory(initialdir=v["folder"].get()) or v["folder"].get())).grid(row=r, column=2)
        r += 1
        ttk.Label(f, text="عدد الاتصالات لكل ملف:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(f, from_=1, to=32, width=6, textvariable=v["threads"]).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(f, text="تحميلات متزامنة:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(f, from_=1, to=10, width=6, textvariable=v["max_active"]).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Label(f, text="حد السرعة العام:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Combobox(f, textvariable=v["limit"], values=self.SPEED_PRESETS, width=14).grid(row=r, column=1, sticky="w")
        r += 1
        for key, label in (("autostart", "تشغيل PyDM تلقائياً عند بدء ويندوز (يبدأ مصغّراً)"),
                           ("auto_categories", "ترتيب الملفات تلقائياً في مجلدات حسب النوع"),
                           ("auto_retry", "إعادة المحاولة تلقائياً عند الأخطاء"),
                           ("auto_resume", "استئناف التحميلات غير المكتملة عند فتح البرنامج"),
                           ("sound", "صوت عند اكتمال التحميل"),
                           ("clipboard_monitor", "مراقبة الحافظة واقتراح تحميل الروابط المنسوخة")):
            ttk.Checkbutton(f, text=label, variable=v[key]).grid(row=r, column=0, columnspan=3, sticky="w", pady=1)
            r += 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=6)
        r += 1
        ttk.Checkbutton(f, text="تفعيل التكامل مع المتصفح (إضافة Chrome / Edge / Firefox)",
                        variable=v["server_enabled"]).grid(row=r, column=0, columnspan=3, sticky="w")
        r += 1
        ttk.Label(f, text="منفذ التكامل:").grid(row=r, column=0, sticky="w", pady=3)
        ttk.Spinbox(f, from_=1024, to=65535, width=8, textvariable=v["server_port"]).grid(row=r, column=1, sticky="w")
        r += 1
        ttk.Checkbutton(f, text="تجاهل أخطاء شهادة SSL (غير آمن - فقط إذا فشل الاتصال في ويندوز قديم)",
                        variable=v["insecure_ssl"]).grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        r += 1

        def save():
            kb = parse_speed(v["limit"].get())
            if kb is None:
                messagebox.showwarning("قيمة غير صحيحة", "حد السرعة غير صالح", parent=win)
                return
            old_server = (CFG["server_enabled"], CFG["server_port"])
            old_ssl = CFG["insecure_ssl"]
            try:
                CFG["folder"] = v["folder"].get() or CFG["folder"]
                CFG["threads"] = max(1, min(32, int(v["threads"].get())))
                CFG["max_active"] = max(1, min(10, int(v["max_active"].get())))
                CFG["server_port"] = int(v["server_port"].get())
            except (ValueError, tk.TclError):
                messagebox.showwarning("قيمة غير صحيحة", "تحقق من الأرقام المدخلة", parent=win)
                return
            CFG["speed_limit_kb"] = kb
            for k in ("auto_categories", "auto_retry", "auto_resume", "sound", "clipboard_monitor",
                      "server_enabled", "insecure_ssl"):
                CFG[k] = bool(v[k].get())
            CFG.save()
            if v["autostart"].get() != get_autostart():
                if not set_autostart(bool(v["autostart"].get())) and _is_windows():
                    messagebox.showwarning("PyDM", "تعذّر تعديل التشغيل التلقائي مع ويندوز", parent=win)
            GLOBAL_LIMITER.set_rate(kb * 1024)
            self.limit_var.set(fmt_limit(kb))
            self.folder_var.set(CFG["folder"])
            if old_ssl != CFG["insecure_ssl"]:
                configure_net(CFG["insecure_ssl"])
            if old_server != (CFG["server_enabled"], CFG["server_port"]):
                self._start_server()
            win.destroy()
        ttk.Button(f, text="حفظ", command=save).grid(row=r, column=0, columnspan=3, pady=(12, 0))

    def extension_help(self):
        text = (
            "إضافة المتصفح تلتقط التحميلات وترسلها إلى PyDM تلقائياً (مع الكوكيز لتعمل المواقع التي تحتاج تسجيل دخول).\n\n"
            "1) اضغط الزر المناسب لمتصفحك لاستخراج مجلد الإضافة.\n\n"
            "Chrome / Edge / Brave / Opera:\n"
            "   افتح chrome://extensions (أو edge://extensions) ← فعّل «وضع المطور»\n"
            "   ← «تحميل إضافة غير مفعّلة / Load unpacked» ← اختر المجلد المستخرج.\n\n"
            "Firefox:\n"
            "   افتح about:debugging#/runtime/this-firefox ← «تحميل إضافة مؤقتة»\n"
            "   ← اختر الملف manifest.json داخل المجلد المستخرج.\n\n"
            "تأكد أن التكامل مفعّل في الإعدادات (المنفذ %s)." % CFG["server_port"])
        win = tk.Toplevel(self)
        win.title("تثبيت إضافة المتصفح")
        win.transient(self)
        ttk.Label(win, text=text, justify="left", padding=14).pack()
        row = ttk.Frame(win)
        row.pack(pady=(0, 12))

        def export(kind):
            d = filedialog.askdirectory(parent=win, title="اختر مكان حفظ مجلد الإضافة")
            if not d:
                return
            try:
                dst = export_extension(kind, d)
            except Exception as e:
                messagebox.showerror("PyDM", "تعذّر الاستخراج: %s" % e, parent=win)
                return
            open_path(dst)
            self.flash("تم استخراج الإضافة إلى: " + dst)
        ttk.Button(row, text="استخراج إضافة Chrome / Edge / Brave", command=lambda: export("chrome")).pack(side="left", padx=4)
        ttk.Button(row, text="استخراج إضافة Firefox", command=lambda: export("firefox")).pack(side="left", padx=4)

    # ----- الإغلاق
    def on_close(self, force=False):
        if not force and any(t.status in ACTIVE for t in self.tasks.values()):
            if not messagebox.askyesno("PyDM", "هناك تحميلات جارية. سيتم إيقافها وتستكمل عند فتح البرنامج. الخروج؟"):
                return
        self.save_tasks()          # يحفظ الحالات (النشطة تُحفظ كـ «في الانتظار» لتستأنف تلقائياً)
        for t in self.tasks.values():
            t.stop.set()
        if self.server:
            self.server.stop()
        self.destroy()


def enable_dpi():
    if os.name == "nt":
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)   # ويندوز 8.1+
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    global CFG
    if tk is None:
        print("tkinter غير مثبّت. على ويندوز أعد تثبيت Python مع خيار tcl/tk.")
        sys.exit(1)
    enable_dpi()
    if not single_instance():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "PyDM يعمل بالفعل (ابحث عنه في شريط المهام).", "PyDM", 0x40)
        except Exception:
            pass
        sys.exit(0)
    CFG = Config(os.path.join(data_dir(), "config.json"))
    configure_net(CFG["insecure_ssl"])
    app = App()
    if "--minimized" in sys.argv:
        app.iconify()
    app.mainloop()


if __name__ == "__main__":
    main()
