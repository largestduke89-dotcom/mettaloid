; Inno Setup script for Mettaloid (native build).
; Built by scripts/build.py:  ISCC /DAppVersion=x.y.z installer\mettaloid.iss
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8E3C2B71-5D7A-4C7B-9E41-6F1D3A2B9C10}
AppName=Mettaloid
AppVersion={#AppVersion}
AppPublisher=Mettaloid
DefaultDirName={localappdata}\Programs\Mettaloid
DefaultGroupName=Mettaloid
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
LicenseFile=..\assets\license.txt
OutputDir=..\release
OutputBaseFilename=Mettaloid-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Mettaloid.exe
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=2
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startup"; Description: "Start Mettaloid with Windows (in the tray)"; GroupDescription: "Startup:"

[Files]
Source: "..\dist\Mettaloid\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; clean out old bundled runtimes on upgrade (config is regenerated on launch)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\Mettaloid"; Filename: "{app}\Mettaloid.exe"
Name: "{group}\Uninstall Mettaloid"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Mettaloid"; Filename: "{app}\Mettaloid.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Mettaloid"; ValueData: """{app}\Mettaloid.exe"" --hidden"; Flags: uninsdeletevalue; Tasks: startup

[Run]
; interactive install: offer to launch
Filename: "{app}\Mettaloid.exe"; Description: "Launch Mettaloid"; Flags: nowait postinstall skipifsilent
; silent auto-update: relaunch automatically
Filename: "{app}\Mettaloid.exe"; Parameters: "{param:mtargs|}"; Flags: nowait skipifnotsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM obs64.exe /F"; Flags: runhidden; RunOnceId: "KillObs"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\vendor\obs\config"
