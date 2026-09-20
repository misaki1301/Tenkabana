; Tenkabana installer - Inno Setup 6
;
; Build:
;   dotnet publish Tenkabana.csproj -c Release -r win-x64 --self-contained true ^
;       -p:PublishSingleFile=true -p:EnableCompressionInSingleFile=true -o publish\sc
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" Installer\Tenkabana.iss
;
; Self-contained, so the user needs no .NET runtime. ~68 MB.

#define AppName      "Tenkabana"
#define AppVersion   "1.0.0"
#define AppPublisher "misaki1301"
#define AppURL       "https://github.com/misaki1301/Tenkabana"
#define AppExe       "Tenkabana.exe"

[Setup]
AppId={{9F3C2A41-5D7E-4B18-9C6A-TENKABANA0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\publish\installer
OutputBaseFilename=Tenkabana-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; per-user install needs no admin rights
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\publish\sc\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\publish\sc\*.dll";     DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\settings.sample.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";            DestDir: "{app}"; Flags: ignoreversion isreadme

; Character portraits are extracted game art and are NOT shipped. The app falls
; back to a gradient tile without them. For a personal build only, uncomment:
; Source: "..\Assets\portraits\*.png"; DestDir: "{app}\Assets\portraits"; Flags: ignoreversion

; AssetStudioModCLI is MIT licensed, so bundling it is permitted provided its
; licence travels with it. Drop its folder in Installer\astudio\ to include it.
Source: "astudio\*"; DestDir: "{userprofile}\prism_tools\astudio"; \
    Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
// The app is useless without AssetStudioModCLI; warn rather than fail silently.
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  CliDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    CliDir := ExpandConstant('{userprofile}\prism_tools\astudio');
    if not DirExists(CliDir) then
      MsgBox('Tenkabana needs AssetStudioModCLI to decode textures and meshes.' #13#10 #13#10
             'Download it from github.com/aelurum/AssetStudioMod and extract to:' #13#10
             + CliDir + #13#10 #13#10
             'Browsing and extracting still work without it; preview and the '
             'Blender conversion do not.', mbInformation, MB_OK);
  end;
end;
