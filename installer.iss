; مثبّت احترافي (PyDM-Setup.exe) باستخدام Inno Setup 6 (مجاني: jrsoftware.org)
; الخطوات: ابنِ dist\PyDM.exe أولاً (build_exe.bat) ← افتح هذا الملف في Inno Setup ← Compile
[Setup]
AppName=PyDM
AppVersion=2.0
DefaultDirName={autopf}\PyDM
DefaultGroupName=PyDM
OutputBaseFilename=PyDM-Setup
OutputDir=dist
SetupIconFile=pydm.ico
UninstallDisplayIcon={app}\PyDM.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64

[Tasks]
Name: "startup"; Description: "Run PyDM when Windows starts"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "dist\PyDM.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "extension_chrome\*"; DestDir: "{app}\extension_chrome"; Flags: recursesubdirs ignoreversion
Source: "extension_firefox\*"; DestDir: "{app}\extension_firefox"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\PyDM"; Filename: "{app}\PyDM.exe"
Name: "{autodesktop}\PyDM"; Filename: "{app}\PyDM.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PyDM"; ValueData: """{app}\PyDM.exe"" --minimized"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\PyDM.exe"; Description: "Launch PyDM"; Flags: nowait postinstall skipifsilent
