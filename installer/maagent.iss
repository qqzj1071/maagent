; Maagent installer (per-user, no admin needed to install).
; Build:  ISCC.exe installer\maagent.iss
; The app itself runs elevated (its exe is built with --uac-admin), so the
; shortcut will prompt UAC at launch.

#define MyAppName "Maagent"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "qqzj1071"
#define MyAppURL "https://github.com/qqzj1071/maagent"
#define MyAppExeName "maagent.exe"
#define MyAppId "858EEE43-0953-40B1-9E09-A3A3689A2173"
#define RepoRoot AddBackslash(SourcePath) + ".."

[Setup]
AppId={{{#MyAppId}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion=0.1.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Maagent 二游日常助手 安装程序
DefaultDirName={localappdata}\Programs\Maagent
DefaultGroupName=Maagent
DisableProgramGroupPage=yes
DisableDirPage=auto
AllowNoIcons=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#RepoRoot}\dist
OutputBaseFilename=Maagent-Setup-{#MyAppVersion}
SetupIconFile={#RepoRoot}\maagent\gui\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl,compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："
Name: "autostart"; Description: "开机自动启动 Maagent"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "{#RepoRoot}\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\installer\bundle\config\config.yaml"; DestDir: "{app}\config"; Flags: onlyifdoesntexist
Source: "{#RepoRoot}\dist\web\*"; DestDir: "{app}\web"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Maagent"; Filename: "{app}\{#MyAppExeName}"; Comment: "二游日常助手"
Name: "{group}\卸载 Maagent"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Maagent"; Filename: "{app}\{#MyAppExeName}"; Comment: "二游日常助手"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Maagent"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 Maagent"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\config"
Type: filesandordirs; Name: "{app}\web"
