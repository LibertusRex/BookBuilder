; Instalador do BookBuilder (Inno Setup 6).
; Compile com:  ISCC.exe scripts\instalador.iss   (depois de gerar dist\BookBuilder)

#define AppName "BookBuilder"
#define AppVersion "0.1.0"

[Setup]
AppId={{6E0F7A51-3C1B-4B7E-9C2E-B00C8B0E1D21}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=BookBuilder
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\dist\instalador
OutputBaseFilename=BookBuilder-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
UninstallDisplayIcon={app}\BookBuilder.exe
MinVersion=10.0.17763

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos:"

[Files]
Source: "..\dist\BookBuilder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\BookBuilder.exe"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\BookBuilder.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\BookBuilder.exe"; Description: "Abrir o BookBuilder agora"; Flags: nowait postinstall skipifsilent
