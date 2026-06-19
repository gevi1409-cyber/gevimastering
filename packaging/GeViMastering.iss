#ifndef SourceDir
  #define SourceDir "..\.build\dist\GeViMastering"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif

[Setup]
AppId={{5D53EF8C-96A8-4A31-A02E-8072C83A6617}
AppName=GeVi Mastering
AppVersion=1.0.0
AppPublisher=GeVi
DefaultDirName={localappdata}\Programs\GeViMastering
DefaultGroupName=GeVi Mastering
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=GeViMastering-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\GeViMastering.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GeVi Mastering"; Filename: "{app}\GeViMastering.exe"
Name: "{autodesktop}\GeVi Mastering"; Filename: "{app}\GeViMastering.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Run]
Filename: "{app}\GeViMastering.exe"; Description: "Abrir GeVi Mastering"; Flags: nowait postinstall skipifsilent
