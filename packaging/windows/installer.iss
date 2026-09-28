; Inno Setup script for the BI Documentation Generator (B09).
; Build: iscc /DAppVersion=0.2.0 /DSourceDir=..\..\dist\bidoc /DOutputDir=..\..\dist\installer installer.iss
;
; Per-user install (no administrator rights), fixed AppId so a newer version upgrades in
; place. Program files live in the install folder; history, settings and workspaces live in
; %LOCALAPPDATA%\bidoc and are never removed unless the person explicitly asks at uninstall.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef BuildLabel
  #define BuildLabel "development build (unsigned)"
#endif
#define AppName "BI Documentation Generator"

[Setup]
AppId={{6F1B2C94-3D7E-4B8A-9E51-B0C7D2A4F8E3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion} ({#BuildLabel})
AppPublisher=BI Documentation Platform
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} setup, {#BuildLabel}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=bidoc-setup-{#AppVersion}-unsigned
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\BI Documentation Generator.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
InfoBeforeFile=install-notes.txt

[Files]
; ignoreversion + a full replacement of the program folder on upgrade (see [InstallDelete]).
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Remove the previous version's program files before copying the new ones, so no stale
; modules remain. User data is not in {app}.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\BI Documentation Generator.exe"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\BI Documentation Generator.exe"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DeleteUserData: Boolean;

function InitializeUninstall(): Boolean;
begin
  DeleteUserData := False;
  if not UninstallSilent() then
    DeleteUserData := MsgBox('Also delete your generation history, settings and temporary workspaces in ' +
      ExpandConstant('{localappdata}\bidoc') + '?' + #13#10#13#10 +
      'Generated documentation in your output folders is never deleted.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and DeleteUserData then
    DelTree(ExpandConstant('{localappdata}\bidoc'), True, True, True);
end;
