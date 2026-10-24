(async function () {
  var cfg = await getCfg();
  var alive = await ping(cfg.port);
  var dot = document.getElementById("dot");
  var state = document.getElementById("state");
  dot.className = "dot " + (alive ? "on" : "off");
  state.textContent = alive ? "PyDM متصل" : "PyDM غير متصل (شغّل البرنامج وتأكد من المنفذ " + cfg.port + ")";

  var cb = document.getElementById("intercept");
  cb.checked = !!cfg.intercept;
  cb.addEventListener("change", async function () {
    var c = await getCfg();
    c.intercept = cb.checked;
    await setCfg(c);
  });

  document.getElementById("opts").addEventListener("click", function () {
    api.runtime.openOptionsPage();
    window.close();
  });

  var tabs = await api.tabs.query({ active: true, currentWindow: true });
  var tab = tabs[0];
  var data = (await api.storage.local.get("media")).media || {};
  var items = (tab && data[tab.id]) || [];
  var list = document.getElementById("list");
  if (!items.length) {
    var e = document.createElement("div");
    e.className = "empty";
    e.textContent = "لا يوجد. شغّل الفيديو أو الصوت في الصفحة ثم افتح هذه النافذة.";
    list.appendChild(e);
    return;
  }
  items.forEach(function (it) {
    var box = document.createElement("div");
    box.className = "item";
    var name = "media";
    try { name = decodeURIComponent(new URL(it.url).pathname.split("/").pop()) || "media"; } catch (e) {}
    var n = document.createElement("div"); n.className = "n"; n.textContent = name;
    var m = document.createElement("div"); m.className = "m"; m.textContent = it.type + (it.size ? "  •  " + fmtSize(it.size) : "");
    var b = document.createElement("button"); b.textContent = "تحميل بواسطة PyDM"; b.disabled = !alive;
    b.addEventListener("click", async function () {
      try {
        await sendUrl(it.url, tab.url);
        b.textContent = "تم الإرسال ✓"; b.disabled = true;
      } catch (err) {
        b.textContent = "فشل الإرسال";
      }
    });
    box.appendChild(n); box.appendChild(m); box.appendChild(b);
    list.appendChild(box);
  });
})();
