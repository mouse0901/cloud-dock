; Stage is derived from AppVersion below.
#define Build "..\work\desktop-build"
#ifndef AppVersion
#define AppVersion "0.6.3"
#endif
#undef Stage
#define Stage "H:\CodexOutputs\packages\cloud-dock\" + AppVersion + "\CloudDock-" + AppVersion + "-Windows-x64"
#ifndef OutputDir
#define OutputDir "H:\CodexOutputs\packages\cloud-dock\" + AppVersion
#endif
[Setup]
AppId={{28AE1E10-53D3-43F6-9B41-E39A7D629C61}
AppName=云屿 Cloud Dock
AppVersion={#AppVersion}
AppVerName=云屿 Cloud Dock {#AppVersion}
DefaultDirName={localappdata}\Programs\CloudDock
DefaultGroupName=云屿 Cloud Dock
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes
AllowNoIcons=yes
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\CloudDock.exe
SetupIconFile=..\assets\branding\cloud-dock.ico
OutputDir={#OutputDir}
OutputBaseFilename=CloudDock-{#AppVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
SetupLogging=yes
InfoBeforeFile=release-notes.txt

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; Flags: unchecked
[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#Build}\ReleaseHelper.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Build}\UpdateLauncher.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Build}\ReleaseHelper.exe"; Flags: dontcopy
Source: "{#Build}\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy
[Icons]
Name: "{group}\云屿"; Filename: "{app}\CloudDock.exe"
Name: "{group}\卸载云屿"; Filename: "{uninstallexe}"
Name: "{autodesktop}\云屿"; Filename: "{app}\CloudDock.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\CloudDock.exe"; Description: "打开云屿"; Flags: nowait postinstall skipifsilent; Check: ImportSucceeded
[Code]
var
  DataPage: TInputDirWizardPage;
  ImportPage: TInputQueryWizardPage;
  ImportBrowse: TNewButton;
  DataLocation, LegacyLocation: String;
  ImportOK: Boolean;
  MaintenancePath: String;
function GetCurrentProcessId: LongWord; external 'GetCurrentProcessId@kernel32.dll stdcall';

function Q(const S: String): String;
begin Result := '"' + S + '"'; end;
function ReadData(const AppFolder: String): String;
var S: AnsiString;
begin
  Result := '';
  if LoadStringFromFile(AddBackslash(AppFolder)+'installed-data.txt', S) then begin Result := Trim(UTF8Decode(S));if (Length(Result)>0) and (Ord(Result[1])=65279) then Delete(Result,1,1);end;
end;
function WebViewPresent: Boolean;
var V: String; Key: String;
begin
  Key := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := (RegQueryStringValue(HKCU,Key,'pv',V) and (V<>'') and (V<>'0.0.0.0')) or
    (RegQueryStringValue(HKLM32,Key,'pv',V) and (V<>'') and (V<>'0.0.0.0'));
end;
function StopAndCheck(const DataFolder, AppFolder: String): Boolean;
var Code: Integer;
begin
  Result := Exec(ExpandConstant('{tmp}\ReleaseHelper.exe'),'exit '+Q(DataFolder)+' '+Q(AppFolder),'',SW_HIDE,ewWaitUntilTerminated,Code) and (Code=0);
end;
procedure ChooseLegacy(Sender: TObject);
var Folder: String;
begin Folder:=ImportPage.Values[0];if BrowseForFolder('选择旧便携版目录',Folder,False) then ImportPage.Values[0]:=Folder;end;
procedure InitializeWizard;
var Saved: String;
begin
  ImportOK := True;
  DataPage := CreateInputDirPage(wpSelectDir,'数据保存位置','登录、设置和任务独立保存，升级与卸载保留此目录。','请选择独立于程序的可写文件夹。下载位置还可以在软件设置中更改。',False,'');
  DataPage.Add('数据目录：');
  Saved:=ExpandConstant('{localappdata}\CloudDock');
  if not RegQueryStringValue(HKCU,'Software\CloudDock','DataDirectory',Saved) or (Saved='') then Saved:=ExpandConstant('{localappdata}\CloudDock');
  DataPage.Values[0]:=ExpandConstant('{param:DATA|'+Saved+'}');
  ImportPage := CreateInputQueryPage(DataPage.ID,'从便携版导入（可选）','首次安装可以保留原来的登录和设置。','选择旧版 CloudDock.exe 所在文件夹；没有旧版请留空。原目录和下载文件保留，导入后的同步和备份先暂停，核对位置后启用。请先退出旧版，导入后使用安装版入口。');
  ImportPage.Add('旧版程序目录（可留空）：',False);
  ImportBrowse:=TNewButton.Create(WizardForm);ImportBrowse.Parent:=ImportPage.Surface;ImportBrowse.Caption:='浏览…';ImportBrowse.Top:=ImportPage.Edits[0].Top+ImportPage.Edits[0].Height+ScaleY(12);ImportBrowse.Left:=ImportPage.Edits[0].Left;ImportBrowse.Width:=ScaleX(90);ImportBrowse.OnClick:=@ChooseLegacy;
  ImportPage.Values[0] := ExpandConstant('{param:IMPORT|}');
end;
procedure CurPageChanged(CurPageID: Integer);
var Existing: String;
begin
  if CurPageID=DataPage.ID then begin
    Existing := ReadData(WizardDirValue);
    if Existing<>'' then begin DataPage.Values[0]:=Existing;DataPage.Edits[0].Enabled:=False;DataPage.Buttons[0].Enabled:=False;end
    else begin DataPage.Edits[0].Enabled:=True;DataPage.Buttons[0].Enabled:=True;end;
  end;
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Existing, A, D: String; Code: Integer;
begin
  Result := '';
  A := AddBackslash(ExpandFileName(WizardDirValue));
  Existing := ReadData(WizardDirValue);
  DataLocation := Trim(DataPage.Values[0]);
  if Existing<>'' then DataLocation:=Existing;
  LegacyLocation := Trim(ImportPage.Values[0]);
  if (Pos('"',DataLocation)>0) or (Length(DataLocation)<4) or (Copy(DataLocation,2,2)<>':\') then begin Result:='请选择有效的本地磁盘数据目录。';exit;end;
  DataLocation:=RemoveBackslashUnlessRoot(ExpandFileName(DataLocation));D:=AddBackslash(DataLocation);
  if (Pos(LowerCase(A),LowerCase(D))=1) or (Pos(LowerCase(D),LowerCase(A))=1) then begin Result:='程序目录与数据目录不可相互包含，请选择独立位置。';exit;end;
  if (Existing='') and FileExists(A+'data\preferences.json') then begin Result:='这里是旧便携版。请选择新的安装位置，并在导入页选择旧目录。';exit;end;
  ExtractTemporaryFile('ReleaseHelper.exe');
  ForceDirectories(ExtractFileDir(DataLocation));
  MaintenancePath:=DataLocation+'.installing';
  if not SaveStringToFile(MaintenancePath,IntToStr(GetCurrentProcessId),False) then begin Result:='无法写入数据位置，请检查权限。';exit;end;
  if not StopAndCheck(DataLocation,WizardDirValue) then begin Result:='云屿仍在保存任务，请从托盘退出后重试。没有强制结束进程或替换文件。';exit;end;
  if LegacyLocation<>'' then begin
    if (Pos('"',LegacyLocation)>0) or (Length(LegacyLocation)<4) or (Copy(LegacyLocation,2,2)<>':\') then begin Result:='请选择有效的旧版本地程序目录。';exit;end;
    if not FileExists(AddBackslash(LegacyLocation)+'data\preferences.json') then begin Result:='旧目录中没有可导入的设置，请重新选择或清空导入目录。';exit;end;
    if DirExists(DataLocation) then begin Result:='数据目录已经存在，请使用新的空位置，避免覆盖已有数据。';exit;end;
    if not StopAndCheck(AddBackslash(LegacyLocation)+'data',LegacyLocation) then begin Result:='请先从托盘退出旧便携版，再继续导入。';exit;end;
  end;
  if not IsDotNetInstalled(net48,0) then begin Result:='需要 .NET Framework 4.8。请安装微软运行环境后重试：https://dotnet.microsoft.com/download/dotnet-framework/net48';exit;end;
  if not WebViewPresent then begin
    if (not WizardSilent) and (MsgBox('缺少 Microsoft WebView2。是否联网安装微软运行环境？',mbConfirmation,MB_YESNO)<>IDYES) then begin Result:='安装已停止，请安装 WebView2 后重试。';exit;end;
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'),'/silent /install','',SW_HIDE,ewWaitUntilTerminated,Code) or not WebViewPresent then begin Result:='WebView2 安装未完成。请检查网络，或从微软官网安装后重试。';exit;end;
  end;
end;
procedure CurStepChanged(CurStep: TSetupStep);
var Code: Integer; Command: String;
begin
  if CurStep=ssDone then begin if MaintenancePath<>'' then DeleteFile(MaintenancePath);end;
  if CurStep=ssPostInstall then begin
    if LegacyLocation<>'' then begin
      SaveStringToFile(ExpandConstant('{app}\import-pending'),'1',False);
      ImportOK := Exec(ExpandConstant('{app}\runtime\python\python.exe'),'-B '+Q(ExpandConstant('{app}\desktop\migrate.py'))+' --source '+Q(LegacyLocation)+' --target '+Q(DataLocation),'',SW_HIDE,ewWaitUntilTerminated,Code) and (Code=0);
      if not ImportOK then begin MsgBox('程序已安装，但数据导入未完成，原数据保留。请重新安装并选择新的数据目录重试；本次不会启动软件。',mbError,MB_OK);end;
    end;
    if ImportOK then begin
      DeleteFile(ExpandConstant('{app}\import-pending'));
      ForceDirectories(DataLocation);
      if not SaveStringToFile(ExpandConstant('{app}\installed-data.txt'),UTF8Encode(DataLocation),False) then RaiseException('无法保存数据目录设置。');
      RegWriteStringValue(HKCU,'Software\CloudDock','DataDirectory',DataLocation);
      if (LegacyLocation<>'') and RegQueryStringValue(HKCU,'Software\Microsoft\Windows\CurrentVersion\Run','CloudDock',Command) and (CompareText(Command,Q(AddBackslash(LegacyLocation)+'CloudDock.exe')+' --startup')=0) then RegWriteStringValue(HKCU,'Software\Microsoft\Windows\CurrentVersion\Run','CloudDock',Q(ExpandConstant('{app}\CloudDock.exe'))+' --startup');
    end;
  end;
end;
procedure DeinitializeSetup;
begin if MaintenancePath<>'' then DeleteFile(MaintenancePath);end;
function GetCustomSetupExitCode: Integer;
begin if ImportOK then Result:=0 else Result:=2;end;
function ImportSucceeded: Boolean;
begin if ImportOK and (MaintenancePath<>'') then DeleteFile(MaintenancePath);Result:=ImportOK; end;
function InitializeUninstall: Boolean;
var Code: Integer; Data, Command: String;
begin
  Data:=ReadData(ExpandConstant('{app}'));
  MaintenancePath:=Data+'.installing';
  SaveStringToFile(MaintenancePath,IntToStr(GetCurrentProcessId),False);
  Result := Exec(ExpandConstant('{app}\ReleaseHelper.exe'),'exit '+Q(Data)+' '+Q(ExpandConstant('{app}')),'',SW_HIDE,ewWaitUntilTerminated,Code) and (Code=0);
  if not Result then begin MsgBox('云屿仍在保存任务，请从托盘退出后重新卸载。',mbError,MB_OK);exit;end;
  if RegQueryStringValue(HKCU,'Software\Microsoft\Windows\CurrentVersion\Run','CloudDock',Command) and (CompareText(Command,Q(ExpandConstant('{app}\CloudDock.exe'))+' --startup')=0) then
    RegDeleteValue(HKCU,'Software\Microsoft\Windows\CurrentVersion\Run','CloudDock');
end;
procedure DeinitializeUninstall;
begin if MaintenancePath<>'' then DeleteFile(MaintenancePath);end;
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep=usPostUninstall then begin
    DeleteFile(ExpandConstant('{app}\installed-data.txt'));
    if not UninstallSilent then MsgBox('云屿程序已卸载。登录、设置、任务记录、下载和同步文件均保留在原位置。',mbInformation,MB_OK);
  end;
end;
