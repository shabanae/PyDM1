// خلفية الإضافة: اعتراض التحميلات + القائمة السياقية + التقاط الفيديو
if (typeof importScripts === "function") {
  try { importScripts("common.js"); } catch (e) {}
}

var MEDIA_MIN = 200 * 1024;
var mediaCache = null;

function badge(tabId, text, color) {
  try {
    var act = api.action || api.browserAction;
    var o = { text: text };
    if (tabId !== undefined && tabId >= 0) o.tabId = tabId;
    act.setBadgeText(o);
    if (color) {
      var c = { color: color };
      if (tabId !== undefined && tabId >= 0) c.tabId = tabId;
      act.setBadgeBackgroundColor(c);
    }
  } catch (e) {}
}

function flashBadge(text, color) {
  badge(undefined, text, color);
  setTimeout(function () { badge(undefined, ""); }, 3000);
}

// ---------- اعتراض التحميلات ----------
function shouldIntercept(cfg, item) {
  if (cfg.interceptAll) return true;
  var exts = cfg.exts.split(",").map(function (s) { return s.trim().toLowerCase(); }).filter(Boolean);
  var url = item.finalUrl || item.url;
  try {
    var m = new URL(url).pathname.match(/\.([a-z0-9]{1,6})$/i);
    if (m && exts.indexOf(m[1].toLowerCase()) >= 0) return true;
  } catch (e) {}
  if (item.filename) {
    var m2 = item.filename.match(/\.([a-z0-9]{1,6})$/i);
    if (m2 && exts.indexOf(m2[1].toLowerCase()) >= 0) return true;
  }
  if (item.fileSize > 0 && item.fileSize >= cfg.minSizeKB * 1024) return true;
  var mime = (item.mime || "").toLowerCase();
  if (/^(video|audio)\//.test(mime)) return true;
  if (/(zip|octet-stream|x-msdownload|x-rar|7z|x-iso|x-msi|x-bittorrent)/.test(mime)) return true;
  return false;
}

api.downloads.onCreated.addListener(async function (item) {
  try {
    var cfg = await getCfg();
    if (!cfg.intercept) return;
    var url = item.finalUrl || item.url;
    if (!/^https?:/i.test(url)) return;            // blob: و data: تبقى في المتصفح
    if (!shouldIntercept(cfg, item)) return;
    if (!(await ping(cfg.port))) return;           // PyDM مغلق → المتصفح يكمل بنفسه
    await api.downloads.cancel(item.id);
    try { await api.downloads.erase({ id: item.id }); } catch (e) {}
    var name = item.filename ? item.filename.split(/[\\/]/).pop() : "";
    try {
      await sendUrl(url, item.referrer, name);
      flashBadge("✓", "#1b7f3b");
    } catch (e) {
      api.downloads.download({ url: url });        // فشل الإرسال → نعيد التحميل داخل المتصفح
    }
  } catch (e) {}
});

// ---------- القائمة السياقية ----------
async function createMenus() {
  try {
    await api.contextMenus.removeAll();
    api.contextMenus.create({
      id: "pydm-link",
      title: "التحميل باستخدام PyDM",
      contexts: ["link", "video", "audio", "image"]
    });
  } catch (e) {}
}
api.runtime.onInstalled.addListener(createMenus);
if (api.runtime.onStartup) api.runtime.onStartup.addListener(createMenus);

api.contextMenus.onClicked.addListener(async function (info, tab) {
  var url = info.linkUrl || info.srcUrl;
  if (!url || !/^https?:/i.test(url)) return;
  try {
    var cfg = await getCfg();
    if (!(await ping(cfg.port))) { flashBadge("!", "#b00020"); return; }
    await sendUrl(url, info.pageUrl || (tab && tab.url));
    flashBadge("✓", "#1b7f3b");
  } catch (e) {
    flashBadge("!", "#b00020");
  }
});

// ---------- التقاط الفيديو/الصوت (مثل IDM) ----------
async function loadMedia() {
  if (!mediaCache) {
    var d = await api.storage.local.get("media");
    mediaCache = d.media || {};
  }
  return mediaCache;
}

api.webRequest.onHeadersReceived.addListener(async function (d) {
  try {
    if (d.tabId < 0) return;
    var cfg = await getCfg();
    if (!cfg.sniff) return;
    var h = {};
    (d.responseHeaders || []).forEach(function (x) { h[x.name.toLowerCase()] = x.value; });
    var ct = (h["content-type"] || "").split(";")[0].trim().toLowerCase();
    if (!(ct.indexOf("video/") === 0 || ct.indexOf("audio/") === 0) || ct === "video/mp2t") return;
    var size = parseInt(h["content-length"] || "0", 10) || 0;
    var cr = h["content-range"];
    if (cr) { var m = cr.match(/\/(\d+)$/); if (m) size = parseInt(m[1], 10); }
    if (size && size < MEDIA_MIN) return;
    var media = await loadMedia();
    var list = media[d.tabId] || (media[d.tabId] = []);
    if (list.some(function (x) { return x.url === d.url; })) return;
    list.unshift({ url: d.url, type: ct, size: size, t: Date.now() });
    if (list.length > 30) list.length = 30;
    await api.storage.local.set({ media: media });
    badge(d.tabId, String(list.length), "#0b5cad");
  } catch (e) {}
}, { urls: ["<all_urls>"], types: ["media", "xmlhttprequest", "other"] }, ["responseHeaders"]);

api.tabs.onUpdated.addListener(async function (tabId, info) {
  if (info.status === "loading" && info.url) {
    var media = await loadMedia();
    if (media[tabId]) {
      delete media[tabId];
      await api.storage.local.set({ media: media });
    }
    badge(tabId, "");
  }
});

api.tabs.onRemoved.addListener(async function (tabId) {
  var media = await loadMedia();
  if (media[tabId]) {
    delete media[tabId];
    await api.storage.local.set({ media: media });
  }
});
