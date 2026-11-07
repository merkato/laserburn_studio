; Instalator LaserBurn Studio (Inno Setup 6). Budowany przez build.py, który przekazuje
; AppVersion, AppName, AppPublisher, SourceDir, OutputDir i ExeName parametrami /D.
; Ręcznie: ISCC /DSourceDir=..\dist\LaserBurnStudio /DOutputDir=..\dist installer\laserburn_studio.iss

#ifndef AppName
  #define AppName "LaserBurn Studio"
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef AppPublisher
  #define AppPublisher "Tomasz Nycz"
#endif
#ifndef ExeName
  #define ExeName "LaserBurnStudio"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\LaserBurnStudio"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{7E3C2B54-9A61-4F0E-B8D2-5C1A0F4E9B37}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Domyślnie instalacja dla bieżącego użytkownika (bez uprawnień administratora);
; w oknie można wybrać instalację dla wszystkich użytkowników.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename={#ExeName}-{#AppVersion}-setup
SetupIconFile=..\assets\laserburn.ico
UninstallDisplayIcon={app}\{#ExeName}.exe
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes
CloseApplications=yes

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "lcamassoc"; Description: "Otwieraj pliki projektów .lcam w {#AppName}"; GroupDescription: "Skojarzenia plików:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}.exe"
Name: "{group}\Instrukcja obsługi"; Filename: "{app}\instrukcja.md"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.lcam"; ValueType: string; ValueName: ""; ValueData: "LaserBurnStudio.Project"; Flags: uninsdeletevalue; Tasks: lcamassoc
Root: HKA; Subkey: "Software\Classes\LaserBurnStudio.Project"; ValueType: string; ValueName: ""; ValueData: "Projekt {#AppName}"; Flags: uninsdeletekey; Tasks: lcamassoc
Root: HKA; Subkey: "Software\Classes\LaserBurnStudio.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#ExeName}.exe,0"; Tasks: lcamassoc
Root: HKA; Subkey: "Software\Classes\LaserBurnStudio.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#ExeName}.exe"" ""%1"""; Tasks: lcamassoc

[Run]
Filename: "{app}\{#ExeName}.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Baza materiałów (%USERPROFILE%\.laserburn_studio) celowo NIE jest usuwana przy deinstalacji.
