// كود مشترك بين الخلفية والنافذة المنبثقة والإعدادات
var api = (typeof browser !== "undefined" && browser.runtime) ? browser : chrome;

var DEFAULTS = {
  port: 6800,
  intercept: true,        // اعتراض التحميلات وإرسالها إلى PyDM
  interceptAll: false,    // اعتراض كل شيء (وليس فقط الأنواع/الأحجام أدناه)
  minSizeKB: 2048,        // أي ملف أكبر من هذا الحجم يُرسل
  sniff: true,            // التقاط روابط الفيديو والصوت في الصفحة
  exts: "zip,rar,7z,tar,gz,bz2,xz,iso,exe,msi,apk,dmg,deb,rpm,mp4,mkv,avi,mov,wmv,flv,webm,mp3,flac,wav,m4a,pdf,epub,doc,docx,xls,xlsx,ppt,pptx,torrent"
};

async function getCfg() {
  var d = await api.storage.local.get("cfg");
  return Object.assign({}, DEFAULTS, d.cfg || {});
}

async function setCfg(c) {
  await api.storage.local.set({ cfg: c });
}

// هل PyDM يعمل؟
async function ping(port) {
  try {
    var ctl = new AbortController();
    var t = setTimeout(function () { ctl.abort(); }, 1200);
    var r = await fetch("http://127.0.0.1:" + port + "/ping", { cache: "no-store", signal: ctl.signal });
    clearTimeout(t);
    var j = await r.json();
    return !!(j && j.app === "PyDM");
  } catch (e) {
    return false;
  }
}

async function cookieHeader(url) {
  try {
    var cs = await api.cookies.getAll({ url: url });
    return cs.map(function (c) { return c.name + "=" + c.value; }).join("; ");
  } catch (e) {
    return "";
  }
}

async function sendUrl(url, referer, filename) {
  var cfg = await getCfg();
  var cookies = await cookieHeader(url);
  var r = await fetch("http://127.0.0.1:" + cfg.port + "/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: url,
      referer: referer || "",
      cookies: cookies,
      userAgent: navigator.userAgent,
      filename: filename || ""
    })
  });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return true;
}

function fmtSize(n) {
  if (!n) return "";
  var u = ["B", "KB", "MB", "GB", "TB"], i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(2) : n.toFixed(0)) + " " + u[i];
}
