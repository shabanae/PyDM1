(async function () {
  var cfg = await getCfg();
  var ids = ["port", "minSizeKB", "exts"], checks = ["intercept", "interceptAll", "sniff"];
  ids.forEach(function (k) { document.getElementById(k).value = cfg[k]; });
  checks.forEach(function (k) { document.getElementById(k).checked = !!cfg[k]; });
  document.getElementById("save").addEventListener("click", async function () {
    var c = await getCfg();
    c.port = parseInt(document.getElementById("port").value, 10) || 6800;
    c.minSizeKB = parseInt(document.getElementById("minSizeKB").value, 10) || 0;
    c.exts = document.getElementById("exts").value;
    checks.forEach(function (k) { c[k] = document.getElementById(k).checked; });
    await setCfg(c);
    var s = document.getElementById("saved");
    s.textContent = "تم الحفظ ✓";
    setTimeout(function () { s.textContent = ""; }, 2000);
  });
})();
