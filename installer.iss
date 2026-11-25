; Installer di PDFlex (Inno Setup 6). Si crea con release.bat, che passa la versione:
;   ISCC.exe /DVersion=1.5 installer.iss   ->   PDFlex-Setup-1.5.exe
; Per l'utente corrente, senza permessi di amministratore, nella stessa cartella di install.ps1:
; chi aveva già PDFlex lo trova aggiornato al suo posto.

#ifndef Version
  #define Version "0.0"
#endif

[Setup]
AppId={{6E0FEF4D-D170-4B97-A208-3472A9117BA3}
AppName=PDFlex
AppVersion={#Version}
AppVerName=PDFlex {#Version}
AppPublisher=PDFlex
AppSupportURL=https://github.com/GabrieleL18/PDFlex
AppUpdatesURL=https://github.com/GabrieleL18/PDFlex/releases
DefaultDirName={localappdata}\Programs\PDFlex
DisableDirPage=auto
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=PDFlex-Setup-{#Version}
SetupIconFile=pdflex.ico
WizardSmallImageFile=wizard-small.bmp
UninstallDisplayIcon={app}\PDFlex.exe
UninstallDisplayName=PDFlex
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes
; the language of Windows, like PDFlex itself: no question
ShowLanguageDialog=no
LanguageDetectionMethod=uilanguage

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
it.OpenWith=Mostra PDFlex in "Apri con" sui file PDF
en.OpenWith=Show PDFlex in "Open with" on PDF files
it.PdfDocument=Documento PDF
en.PdfDocument=PDF document

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "openwith"; Description: "{cm:OpenWith}"

[InstallDelete]
; what an older version left: the program is copied whole each time
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\uninstall.ps1"

[Files]
Source: "dist\PDFlex\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\PDFlex"; Filename: "{app}\PDFlex.exe"
Name: "{userdesktop}\PDFlex"; Filename: "{app}\PDFlex.exe"; Tasks: desktopicon

[Registry]
; installed before with install.ps1: its entry in Settings > Apps goes, this installer has its own
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Uninstall\PDFlex"; ValueType: none; Flags: deletekey
; "Apri con PDFlex" on .pdf files (the default PDF program does not change)
Root: HKCU; Subkey: "Software\Classes\PDFlex.pdf"; ValueType: string; ValueData: "{cm:PdfDocument}"; Flags: uninsdeletekey; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\PDFlex.pdf\DefaultIcon"; ValueType: string; ValueData: """{app}\PDFlex.exe"",0"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\PDFlex.pdf\shell\open\command"; ValueType: string; ValueData: """{app}\PDFlex.exe"" ""%1"""; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "PDFlex.pdf"; ValueData: ""; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\PDFlex.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "PDFlex"; Flags: uninsdeletekey; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\PDFlex.exe\shell\open\command"; ValueType: string; ValueData: """{app}\PDFlex.exe"" ""%1"""; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\PDFlex.exe\SupportedTypes"; ValueType: string; ValueName: ".pdf"; ValueData: ""; Tasks: openwith

[Run]
Filename: "{app}\PDFlex.exe"; Description: "{cm:LaunchProgram,PDFlex}"; Flags: nowait postinstall skipifsilent
