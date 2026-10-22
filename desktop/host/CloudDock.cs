using System;
using System.IO;
using System.Net;
using System.Text;
using System.Drawing;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Runtime.InteropServices;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

static class Program {
    static Mutex instance;
    public static EventWaitHandle Activation, ExitRequest;
    public static string Data;
    static FileStream dataLock;
    public static Stopwatch Clock=Stopwatch.StartNew();
    [DllImport("user32.dll")] static extern bool AllowSetForegroundWindow(int processId);
    [STAThread] static void Main(string[] args) {
        if(File.Exists(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"import-pending"))){MessageBox.Show("上次数据导入未完成，请重新运行安装程序完成导入。原数据仍保留。","云屿");return;}
        bool startup=Array.IndexOf(args,"--startup")>=0;
        string root=AppDomain.CurrentDomain.BaseDirectory, name;
        try{Data=RuntimePaths.Data(root);RuntimePaths.Prepare(Data);}catch{MessageBox.Show("正在安装更新，或无法打开数据目录。请等待安装结束，并确认数据位置可写。","云屿");return;}
        name=RuntimePaths.Key(Data);
        if(Array.IndexOf(args,"--request-exit")>=0){Environment.ExitCode=RuntimePaths.RequestExit(Data,root);return;}
        if(Array.IndexOf(args,"--remove-startup")>=0){new StartupRegistration(Path.Combine(root,"CloudDock.exe")).Set(false);return;}
        ExitRequest=new EventWaitHandle(false,EventResetMode.AutoReset,"Local\\CloudDock-Exit-"+name);
        Activation=new EventWaitHandle(false,EventResetMode.AutoReset,"Local\\CloudDock-Activate-"+name);
        bool created;instance=new Mutex(true,"Local\\CloudDock-"+name,out created);
        if(!created){
            foreach(var p in Process.GetProcessesByName("CloudDock"))try{
                if(p.Id!=Process.GetCurrentProcess().Id && String.Equals(p.MainModule.FileName,Path.Combine(root,"CloudDock.exe"),StringComparison.OrdinalIgnoreCase))
                    AllowSetForegroundWindow(p.Id);
            }catch{}finally{p.Dispose();}
            if(!startup)Activation.Set();Activation.Dispose();instance.Dispose();return;
        }
        try{dataLock=new FileStream(Path.Combine(Data,".app-lock"),FileMode.OpenOrCreate,FileAccess.ReadWrite,FileShare.None);}catch{MessageBox.Show("另一个云屿后台仍在保存数据，请稍后重试。","云屿");instance.ReleaseMutex();instance.Dispose();return;}
        Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
        try{Application.Run(new CloudWindow(root,startup));}finally{dataLock.Dispose();ExitRequest.Dispose();Activation.Dispose();instance.ReleaseMutex();instance.Dispose();}
    }
}

partial class CloudWindow:Form {
    WebView2 view=new WebView2();
    readonly BrowserRecovery recovery=new BrowserRecovery();
    bool browserBroken,recovering;
    int readyCount;
    string lastView,lastProvider;
    readonly MotionSplash loading;
    readonly NotifyIcon tray=new NotifyIcon();
    readonly JavaScriptSerializer json=new JavaScriptSerializer();
    readonly System.Windows.Forms.Timer watchdog=new System.Windows.Forms.Timer();
    readonly string root;
    const string AppOrigin=NativeBridgePolicy.PageOrigin;
    const string BridgeOrigin=NativeBridgePolicy.BridgeOrigin;
    string url,nativeKey;
    bool closing,done,picking,ready,setupRunning,closeToTray=true,trayHintSeen,openSettingsPending,qaStarted;
    bool dark,reduced;
    long navigationStarted;
    Process backend;
    Task<Dictionary<string,object>> backendTask;
    Task browserTask;
    RegisteredWaitHandle activationWait,exitWait;
    readonly HashSet<string> milestones=new HashSet<string>();
    Dictionary<string,object> qaBridgeReply;
    readonly StartupRegistration startupRegistration;
    readonly bool startupLaunch;
    readonly System.Windows.Forms.Timer clipboardTimer=new System.Windows.Forms.Timer();
    readonly ShareLinkHistory clipboardHistory=new ShareLinkHistory();
    bool clipboardEnabled=true,clipboardWake=true,clipboardRegistered,clipboardObserved;
    List<ShareLink> pendingClipboard;
    uint clipboardSequence;
    int clipboardRetries;
    DateTime clipboardChanged=DateTime.UtcNow;
    [DllImport("user32.dll")] static extern bool AddClipboardFormatListener(IntPtr window);
    [DllImport("user32.dll")] static extern bool RemoveClipboardFormatListener(IntPtr window);
    [DllImport("user32.dll")] static extern uint GetClipboardSequenceNumber();
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    static string Q(string value){return "\""+(value??"").Replace("\"","\\\"")+"\"";}

    public CloudWindow(string folder,bool startup=false){
        startupLaunch=startup;startupRegistration=new StartupRegistration(Path.Combine(folder,"CloudDock.exe"));
        root=folder;url=Environment.GetEnvironmentVariable("CLOUD_DOCK_URL");nativeKey=Environment.GetEnvironmentVariable("CLOUD_DOCK_NATIVE_KEY");
        Text="云屿 Cloud Dock · 1.0.1 测试版";var area=Screen.FromPoint(Cursor.Position).WorkingArea;
        ClientSize=new Size(Math.Min(1280,Math.Max(640,area.Width-64)),Math.Min(740,Math.Max(480,area.Height-90)));
        MinimumSize=new Size(Math.Min(980,area.Width-32),Math.Min(680,area.Height-40));StartPosition=FormStartPosition.CenterScreen;
        Icon=new Icon(Path.Combine(root,"assets","branding","cloud-dock.ico"));
        ReadPreferences();
        if(startupLaunch){WindowState=FormWindowState.Minimized;ShowInTaskbar=false;}
        // Keep the browser visible under the native splash so first paint is not throttled.
        view.Dock=DockStyle.Fill;Controls.Add(view);
        loading=new MotionSplash(Path.Combine(root,"assets","branding","cloud-dock.ico"));
        loading.Dock=DockStyle.Fill;loading.SetAppearance(dark,reduced);Controls.Add(loading);loading.BringToFront();
        loading.Retry+=async(s,e)=>{await Setup();};loading.ExitRequested+=(s,e)=>RequestExit();
        loading.FirstPaint+=async(s,e)=>{
            Timing("first_frame");
            string frame=Environment.GetEnvironmentVariable("CLOUD_DOCK_QA_SPLASH");
            if(!String.IsNullOrEmpty(frame)){await Task.Delay(200);if(!IsDisposed && loading.Visible)using(var bitmap=new Bitmap(loading.Width,loading.Height)){loading.DrawToBitmap(bitmap,loading.ClientRectangle);bitmap.Save(frame,System.Drawing.Imaging.ImageFormat.Png);}}
        };
        tray.Icon=Icon;tray.Text="云屿 Cloud Dock";
        var menu=new ContextMenuStrip();
        menu.Items.Add("打开云屿",null,(s,e)=>RestoreWindow(false));
        menu.Items.Add("设置",null,(s,e)=>RestoreWindow(true));
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("退出云屿",null,(s,e)=>RequestExit());
        tray.ContextMenuStrip=menu;tray.DoubleClick+=(s,e)=>RestoreWindow(false);tray.Visible=true;
        watchdog.Interval=500;watchdog.Tick+=(s,e)=>{
            if(!ready && !closing && navigationStarted>0 && Program.Clock.ElapsedMilliseconds-navigationStarted>25000){
                loading.ShowFailure("界面准备时间较长，可以重试。",true);navigationStarted=0;
            }
        };watchdog.Start();
        Shown+=async(s,e)=>{
            Timing("window_shown");
            activationWait=ThreadPool.RegisterWaitForSingleObject(Program.Activation,(state,timedOut)=>{
                try{if(!IsDisposed)BeginInvoke((Action)(()=>RestoreWindow(false)));}catch(InvalidOperationException){}
            },null,Timeout.Infinite,false);
            exitWait=ThreadPool.RegisterWaitForSingleObject(Program.ExitRequest,(state,timedOut)=>{try{if(!IsDisposed)BeginInvoke((Action)RequestExit);}catch(InvalidOperationException){}},null,Timeout.Infinite,false);
            if(startupLaunch){Hide();loading.SetActive(false);backendTask=StartBackend();try{await EnsureService();Timing("background_ready");}catch{tray.ShowBalloonTip(5000,"云屿","后台启动未完成，请打开窗口重试。",ToolTipIcon.Warning);}return;}
            await Setup();
        };
        Resize+=(s,e)=>SetForegroundState(Visible && WindowState!=FormWindowState.Minimized);
        clipboardTimer.Interval=200;clipboardTimer.Tick+=(s,e)=>ReadClipboardLink();
        Activated+=(s,e)=>ScheduleClipboard();
        InitializeFeedback();
        FormClosing+=OnClosing;
        FormClosed+=(s,e)=>{clipboardTimer.Stop();clipboardTimer.Dispose();watchdog.Stop();watchdog.Dispose();if(activationWait!=null)activationWait.Unregister(null);if(exitWait!=null)exitWait.Unregister(null);tray.Visible=false;tray.Dispose();};
    }
    protected override void OnHandleCreated(EventArgs e){base.OnHandleCreated(e);clipboardSequence=GetClipboardSequenceNumber();clipboardObserved=true;clipboardRegistered=AddClipboardFormatListener(Handle);}
    protected override void OnHandleDestroyed(EventArgs e){if(clipboardRegistered){RemoveClipboardFormatListener(Handle);clipboardRegistered=false;}base.OnHandleDestroyed(e);}
    protected override void WndProc(ref Message m){
        if(m.Msg==0x031D){clipboardChanged=DateTime.UtcNow;ScheduleClipboard();}
        base.WndProc(ref m);
    }
    void ScheduleClipboard(){
        if(clipboardTimer==null||closing||!clipboardEnabled)return;
        clipboardRetries=0;clipboardTimer.Stop();clipboardTimer.Start();
    }
    void ReadClipboardLink(){
        clipboardTimer.Stop();
        if(!String.IsNullOrEmpty(Environment.GetEnvironmentVariable("CLOUD_DOCK_QA_LIFECYCLE")))return;
        if(closing||!clipboardEnabled)return;
        if(picking){clipboardTimer.Start();return;}
        uint sequence=GetClipboardSequenceNumber();if(clipboardObserved&&sequence==clipboardSequence)return;
        // If copied while hidden, offer only the latest change within ten minutes.
        if(clipboardObserved&&(DateTime.UtcNow-clipboardChanged).TotalMinutes>10){clipboardSequence=sequence;return;}
        try{
            string text=Clipboard.ContainsText(TextDataFormat.UnicodeText)?Clipboard.GetText(TextDataFormat.UnicodeText):"";
            clipboardObserved=true;clipboardSequence=sequence;
            HandleClipboardText(text);
        }catch(ExternalException){if(++clipboardRetries<4)clipboardTimer.Start();}
    }
    void HandleClipboardText(string text){
        if(closing||!clipboardEnabled)return;
        var links=ShareLink.ParseAll(text);var fresh=new List<ShareLink>();
        foreach(var link in links)if(clipboardHistory.Accept(link))fresh.Add(link);
        if(fresh.Count>0){pendingClipboard=fresh;if(clipboardWake)RestoreWindow(false);SendPendingClipboard();}
        else if(links.Count==0){pendingClipboard=null;Send(new {type="clipboardClear"});}
    }
    void SendPendingClipboard(){
        if(!ready||pendingClipboard==null||!clipboardEnabled)return;
        var links=pendingClipboard;pendingClipboard=null;
        Send(new {type="clipboardShares",links=links});
    }
    void ReadPreferences(){
        try{
            var file=Path.Combine(Program.Data,"preferences.json");
            if(File.Exists(file) && new FileInfo(file).Length<65536){
                var p=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(file));
                closeToTray=Bool(p,"close_to_tray",true);reduced=Bool(p,"reduce_motion",false);
                clipboardEnabled=Bool(p,"clipboard_links",true);
                clipboardWake=Bool(p,"clipboard_wake",true);
                string theme=p.ContainsKey("theme")?Convert.ToString(p["theme"]):"light";
                dark=theme=="dark";
                if(theme=="system")try{dark=Convert.ToInt32(Microsoft.Win32.Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize","AppsUseLightTheme",1))==0;}catch{}
            }
        }catch{}
        trayHintSeen=File.Exists(Path.Combine(Program.Data,"tray-hint-seen"));
    }
    static bool Bool(Dictionary<string,object> d,string key,bool fallback){return d.ContainsKey(key) && d[key] is bool?(bool)d[key]:fallback;}
    void Timing(string phase){
        string file=Environment.GetEnvironmentVariable("CLOUD_DOCK_QA_TIMINGS");
        if(!String.IsNullOrEmpty(file))try{File.AppendAllText(file,phase+"="+Program.Clock.ElapsedMilliseconds+Environment.NewLine);File.AppendAllText(file+".trace",(long)(DateTime.UtcNow-new DateTime(1970,1,1)).TotalMilliseconds+" "+phase+Environment.NewLine);}catch{}
    }
    async Task InitializeBrowser(){
        var env=await CoreWebView2Environment.CreateAsync(null,Path.Combine(Program.Data,"webview"),new CoreWebView2EnvironmentOptions("--disable-features=msEdgeSidebarV2"));
        Timing("webview_environment_ready");
        await view.EnsureCoreWebView2Async(env);Timing("webview_controller_ready");
        view.CoreWebView2.Settings.AreDefaultContextMenusEnabled=false;
        view.CoreWebView2.Settings.AreDevToolsEnabled=false;
        view.CoreWebView2.Settings.IsStatusBarEnabled=false;
        view.CoreWebView2.Settings.IsZoomControlEnabled=true;
        view.CoreWebView2.Settings.AreBrowserAcceleratorKeysEnabled=false;
        view.CoreWebView2.NewWindowRequested+=(s,e)=>{e.Handled=true;};
        view.CoreWebView2.NavigationStarting+=(s,e)=>{if(!Trusted(e.Uri))e.Cancel=true;};
        view.CoreWebView2.PermissionRequested+=(s,e)=>{e.State=CoreWebView2PermissionState.Deny;};
        view.CoreWebView2.DownloadStarting+=(s,e)=>{e.Cancel=true;};
        view.CoreWebView2.WebMessageReceived+=OnMessage;
        view.CoreWebView2.SetVirtualHostNameToFolderMapping("clouddock.invalid",Path.Combine(root,"desktop","web"),CoreWebView2HostResourceAccessKind.Deny);
        view.CoreWebView2.AddWebResourceRequestedFilter(BridgeOrigin+"api/*",CoreWebView2WebResourceContext.All);
        view.CoreWebView2.WebResourceRequested+=ServeLocalAsset;
        view.CoreWebView2.ContentLoading+=(s,e)=>Timing("content_loading");
        view.CoreWebView2.DOMContentLoaded+=(s,e)=>Timing("dom_loaded");
        view.CoreWebView2.NavigationCompleted+=(s,e)=>{
            Timing("navigation_completed");
            if(!e.IsSuccess && !closing)loading.ShowFailure("界面未能加载，请重试。",true);
        };
        view.CoreWebView2.ProcessFailed+=OnBrowserFailure;
    }
    async void OnBrowserFailure(object sender,CoreWebView2ProcessFailedEventArgs e){
        string kind=e.ProcessFailedKind.ToString(),action=BrowserRecovery.Action(kind);
        Timing("webview_failure_"+kind);
        // Enum values and numeric exit code only; never dump crash memory.
        try{
            var file=Path.Combine(Program.Data,"work","webview-recovery.jsonl");
            if(File.Exists(file)&&new FileInfo(file).Length>65536)File.Delete(file);
            File.AppendAllText(file,json.Serialize(new {at=DateTime.UtcNow.ToString("o"),kind=kind,exit=e.ExitCode,action=action})+Environment.NewLine);
        }catch{}
        if(closing||action=="ignore")return;
        if(action=="recreate")browserBroken=true;
        ready=false;loading.Visible=true;loading.BringToFront();
        if(recovering)return;
        if(!recovery.Allow(DateTime.UtcNow)){
            loading.ShowFailure("界面多次中断，后台任务仍保留。请点击重试恢复界面。",true);return;
        }
        recovering=true;loading.Prepare();
        try{
            await Task.Delay(350);
            while(setupRunning&&!closing)await Task.Delay(50);
            if(!closing)await Setup();
        }finally{recovering=false;}
    }
    bool Trusted(string source){return NativeBridgePolicy.IsPage(source);}
    async void ServeLocalAsset(object sender,CoreWebView2WebResourceRequestedEventArgs e){
        if(!e.Request.Uri.StartsWith(BridgeOrigin,StringComparison.Ordinal))return;
        Timing("bridge_request");
        var deferral=e.GetDeferral();
        try{
            var requestUri=new Uri(e.Request.Uri);string path=requestUri.AbsolutePath.TrimStart('/'),method=e.Request.Method;
            bool thumbnail=NativeBridgePolicy.IsThumbnail(path);
            string cors="Access-Control-Allow-Origin: "+AppOrigin.TrimEnd('/')+"\r\nAccess-Control-Allow-Methods: GET, POST\r\nAccess-Control-Allow-Headers: Content-Type\r\nAccess-Control-Max-Age: 600\r\n";
            string source=e.Request.Headers.Contains("Origin")?e.Request.Headers.GetHeader("Origin"):"";
            if(!NativeBridgePolicy.Allows(e.Request.Uri,method,source))throw new InvalidOperationException();
            if(method=="OPTIONS" && path=="api/action" && requestUri.Query==""){
                e.Response=view.CoreWebView2.Environment.CreateWebResourceResponse(null,204,"No Content",cors);return;
            }
            if(requestUri.Query!="" || !((method=="GET"&&(path=="api/state"||thumbnail||NativeBridgePolicy.IsMedia(path)))||(method=="POST"&&path=="api/action")))throw new InvalidOperationException();
            await EnsureService();
            if(NativeBridgePolicy.IsMedia(path)){
                var request=(HttpWebRequest)WebRequest.Create(url+path);request.Proxy=null;request.Timeout=15000;
                if(e.Request.Headers.Contains("Range")){
                    string range=e.Request.Headers.GetHeader("Range");
                    var match=System.Text.RegularExpressions.Regex.Match(range,@"^bytes=(\d*)-(\d*)$");
                    if(!match.Success)throw new InvalidOperationException();
                    if(match.Groups[1].Value=="")request.AddRange(-Int64.Parse(match.Groups[2].Value));
                    else if(match.Groups[2].Value=="")request.AddRange(Int64.Parse(match.Groups[1].Value));
                    else request.AddRange(Int64.Parse(match.Groups[1].Value),Int64.Parse(match.Groups[2].Value));
                }
                HttpWebResponse response;
                try{response=(HttpWebResponse)await request.GetResponseAsync();}
                catch(WebException error){response=error.Response as HttpWebResponse;if(response==null)throw;}
                using(response)using(var input=response.GetResponseStream())using(var buffer=new MemoryStream()){
                    var bytes=new byte[65536];int read;
                    while((read=await input.ReadAsync(bytes,0,bytes.Length))>0){if(buffer.Length+read>134217728)throw new IOException();buffer.Write(bytes,0,read);}
                    string headers=cors+"Content-Type: "+response.ContentType+"\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nAccept-Ranges: bytes\r\nContent-Length: "+buffer.Length+"\r\n";
                    string contentRange=response.Headers[HttpResponseHeader.ContentRange];if(contentRange!=null)headers+="Content-Range: "+contentRange+"\r\n";
                    e.Response=view.CoreWebView2.Environment.CreateWebResourceResponse(new MemoryStream(buffer.ToArray(),false),(int)response.StatusCode,response.StatusDescription,headers);
                }
                return;
            }
            using(var client=new WebClient()){
                client.Proxy=null;byte[] data;string kind="application/json";int status=200;
                try{
                    if(method=="POST"){
                        byte[] payload;
                        using(var buffer=new MemoryStream()){
                            var bytes=new byte[8192];int read;
                            if(e.Request.Content==null)throw new InvalidOperationException();
                            while((read=e.Request.Content.Read(bytes,0,bytes.Length))>0){if(buffer.Length+read>1048576)throw new InvalidOperationException();buffer.Write(bytes,0,read);}
                            payload=buffer.ToArray();
                        }
                        client.Headers[HttpRequestHeader.ContentType]="application/json";
                        data=await client.UploadDataTaskAsync(new Uri(url+path),"POST",payload);
                    }else data=await client.DownloadDataTaskAsync(new Uri(url+path));
                    if(thumbnail){string type=client.ResponseHeaders[HttpResponseHeader.ContentType];kind=type.StartsWith("image/png")?"image/png":type.StartsWith("image/webp")?"image/webp":"image/jpeg";}
                }catch(WebException error){
                    var response=error.Response as HttpWebResponse;
                    if(response==null)throw;status=(int)response.StatusCode;
                    using(response)using(var input=response.GetResponseStream())using(var buffer=new MemoryStream()){input.CopyTo(buffer);data=buffer.ToArray();}
                }
                if(closing||IsDisposed)return;
                e.Response=view.CoreWebView2.Environment.CreateWebResourceResponse(new MemoryStream(data,false),status,status==200?"OK":"Request failed",cors+"Content-Type: "+kind+"\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\n");Timing("bridge_status_"+status);
            }
        }catch(Exception error){Timing("bridge_error_"+error.GetType().Name);if(!IsDisposed && view.CoreWebView2!=null)e.Response=view.CoreWebView2.Environment.CreateWebResourceResponse(new MemoryStream(Encoding.UTF8.GetBytes("{\"error\":\"本地服务暂未就绪，请重试。\"}")),503,"Unavailable","Content-Type: application/json\r\nAccess-Control-Allow-Origin: "+AppOrigin.TrimEnd('/')+"\r\n");}
        finally{deferral.Complete();}
    }
    async Task EnsureService(){
        if(!String.IsNullOrEmpty(url)){
            if(!ValidBackendAddress(url))throw new InvalidOperationException();return;
        }
        if(backendTask==null)throw new InvalidOperationException();
        if(await Task.WhenAny(backendTask,Task.Delay(25000))!=backendTask)throw new TimeoutException();
        var connection=await backendTask;string candidate=Convert.ToString(connection["url"]);
        if(!ValidBackendAddress(candidate))throw new InvalidOperationException();
        url=candidate;nativeKey=Convert.ToString(connection["key"]);
    }
    static bool ValidBackendAddress(string candidate){return NativeBridgePolicy.IsBackend(candidate);}
    async Task Setup(){
        if(setupRunning||closing)return;setupRunning=true;navigationStarted=Program.Clock.ElapsedMilliseconds;
        loading.Visible=true;loading.BringToFront();loading.Prepare();
        try{
            if(browserBroken){
                var old=view;Controls.Remove(old);old.Dispose();
                view=new WebView2();view.Dock=DockStyle.Fill;Controls.Add(view);loading.BringToFront();
                browserTask=null;browserBroken=false;
            }
            // Full browser/controller initialization overlaps the backend.
            if(browserTask==null||browserTask.IsFaulted)browserTask=InitializeBrowser();
            if(String.IsNullOrEmpty(url) && (backendTask==null||backendTask.IsFaulted))backendTask=StartBackend();
            if(await Task.WhenAny(browserTask,Task.Delay(25000))!=browserTask)throw new TimeoutException();
            await browserTask;
            if(closing)return;
            ready=false;navigationStarted=Program.Clock.ElapsedMilliseconds;
            view.CoreWebView2.Navigate(AppOrigin+"index.html");Timing("navigation_started");
        }catch(WebView2RuntimeNotFoundException){
            loading.ShowFailure("缺少 WebView2 运行时，请安装后重试。",true);
            if(MessageBox.Show(this,"需要 Microsoft Edge WebView2 运行时。是否打开微软官方下载页面？","云屿",MessageBoxButtons.YesNo,MessageBoxIcon.Information)==DialogResult.Yes)
                Process.Start(new ProcessStartInfo("https://developer.microsoft.com/microsoft-edge/webview2/"){UseShellExecute=true});
        }catch{if(!closing)loading.ShowFailure("启动未完成，请重试或检查软件是否完整解压。",true);}
        finally{setupRunning=false;}
    }
    async Task<Dictionary<string,object>> StartBackend(){
        Directory.CreateDirectory(Path.Combine(Program.Data,"work"));
        string python=Path.Combine(root,"runtime","python","python.exe");
        if(!File.Exists(python))python=Path.Combine(root,".venv","Scripts","python.exe");
        var start=new ProcessStartInfo(python,"-B \""+Path.Combine(root,"desktop","backend.py")+"\" --pipe-host "+Process.GetCurrentProcess().Id);
        start.WorkingDirectory=root;start.UseShellExecute=false;start.CreateNoWindow=true;start.RedirectStandardOutput=true;start.RedirectStandardError=true;
        start.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";start.EnvironmentVariables["CLOUD_DOCK_DATA_DIR"]=Program.Data;
        backend=Process.Start(start);backend.ErrorDataReceived+=(s,e)=>{};backend.BeginErrorReadLine();
        string line=await backend.StandardOutput.ReadLineAsync();
        if(String.IsNullOrEmpty(line))throw new IOException("Backend exited before handshake");
        var connection=json.Deserialize<Dictionary<string,object>>(line);Timing("service_ready");return connection;
    }
    async Task<Dictionary<string,object>> Post(string route,object data,bool native=false){
        await EnsureService();
        using(var client=new WebClient()){
            client.Proxy=null;client.Headers[HttpRequestHeader.ContentType]="application/json";
            if(native)client.Headers["X-Native-Key"]=nativeKey;
            byte[] response=await client.UploadDataTaskAsync(new Uri(url+route),"POST",Encoding.UTF8.GetBytes(json.Serialize(data)));
            return json.Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(response));
        }
    }
    void Send(object message){if(ready && view.CoreWebView2!=null && !closing)try{view.CoreWebView2.PostWebMessageAsJson(json.Serialize(message));}catch{}}
    void SetForegroundState(bool foreground){
        if(loading!=null)loading.SetActive(foreground);Send(new {type="windowVisibility",visible=foreground});
    }
    async void RestoreWindow(bool settings){
        if(closing)return;
        openSettingsPending|=settings;Show();ShowInTaskbar=true;
        if(WindowState==FormWindowState.Minimized)WindowState=FormWindowState.Normal;
        BringToFront();Activate();SetForegroundWindow(Handle);SetForegroundState(true);Timing("window_restored");
        if(ready && openSettingsPending){Send(new {type="openSettings"});openSettingsPending=false;}
        if(browserTask==null)await Setup();
    }
    void HideToTray(){
        SetForegroundState(false);Hide();ShowInTaskbar=false;Timing("window_to_tray");
        if(!trayHintSeen){
            trayHintSeen=true;
            tray.ShowBalloonTip(4500,"云屿正在后台运行","下载、上传和文件夹备份继续运行。右键托盘图标可退出云屿。",ToolTipIcon.Info);
            try{Directory.CreateDirectory(Program.Data);File.WriteAllText(Path.Combine(Program.Data,"tray-hint-seen"),"1");}catch{}
        }
    }
    async void OnMessage(object sender,CoreWebView2WebMessageReceivedEventArgs e){
        if(closing||!Trusted(e.Source))return;
        string id="";bool ownsPicker=false;
        try{
            var data=json.Deserialize<Dictionary<string,object>>(e.WebMessageAsJson);
            string type=data.ContainsKey("type")?Convert.ToString(data["type"]):"";
            if(type=="uiLocation"){
                string location=data.ContainsKey("view")?Convert.ToString(data["view"]):"";
                string provider=data.ContainsKey("provider")?Convert.ToString(data["provider"]):"";
                if((provider=="baidu"||provider=="quark") && (location=="files"||location=="transfers"||location=="recent"||location=="backups"||location=="trash")){
                    lastView=location;lastProvider=provider;
                }
                return;
            }
            id=data.ContainsKey("id")?Convert.ToString(data["id"]):"";
            if(type=="qaBridgeResult" && !String.IsNullOrEmpty(Environment.GetEnvironmentVariable("CLOUD_DOCK_QA_LIFECYCLE"))){qaBridgeReply=data;return;}
            if(type=="uiReady"){
                ready=true;readyCount++;navigationStarted=0;Timing("shell_interactive");loading.SetActive(false);loading.Visible=false;
                if(lastView!=null&&pendingFeedback==null)Send(new {type="restoreLocation",view=lastView,provider=lastProvider});
                if(pendingFeedback!=null){Send(new {type="feedbackOpen",item=pendingFeedback});pendingFeedback=null;}
                SetForegroundState(Visible && WindowState!=FormWindowState.Minimized);
                if(openSettingsPending){Send(new {type="openSettings"});openSettingsPending=false;}
                ScheduleClipboard();
                SendPendingClipboard();
                if(!qaStarted){qaStarted=true;try{await RunQa();}catch(Exception error){Timing("qa_failed_"+error.GetType().Name);}}return;
            }
            if(type=="startupMilestone"){
                string phase=data.ContainsKey("phase")?Convert.ToString(data["phase"]):"";
                if(phase=="workspace_ready")UpdateRecovery.Healthy(Program.Data,root);
                if((phase=="workspace_ready"||phase=="accounts_settled"||phase=="files_ready")&&milestones.Add(phase))Timing(phase);return;
            }
            if(type=="desktopPreferences"){
                bool nextClipboard=Bool(data,"clipboard_links",clipboardEnabled);
                if(nextClipboard!=clipboardEnabled){clipboardSequence=GetClipboardSequenceNumber();clipboardObserved=true;pendingClipboard=null;}
                closeToTray=Bool(data,"close_to_tray",closeToTray);clipboardEnabled=nextClipboard;clipboardWake=Bool(data,"clipboard_wake",clipboardWake);if(!clipboardEnabled)clipboardTimer.Stop();dark=Bool(data,"dark",dark);reduced=Bool(data,"reduced",reduced);loading.SetAppearance(dark,reduced);return;
            }
            if(type=="openShareExternal"){
                var link=ShareLink.Parse(data.ContainsKey("url")?Convert.ToString(data["url"]):"");
                if(link!=null&&link.provider=="baidu")Process.Start(new ProcessStartInfo(link.url){UseShellExecute=true});return;
            }
            if(await LongevityMessage(type,data))return;
            if(type=="exportDiagnostics"){
                if(picking)return;picking=true;ownsPicker=true;
                using(var save=new SaveFileDialog()){save.Title="导出诊断（不包含账号凭证或文件名）";save.Filter="诊断文件 (*.json)|*.json";save.FileName="CloudDock-diagnostics-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+".json";
                    if(save.ShowDialog(this)==DialogResult.OK){var report=await Post("api/action",new {action="diagnostics"});File.WriteAllText(save.FileName,json.Serialize(report),new UTF8Encoding(false));Send(new {type="diagnosticsSaved"});}}
                return;
            }
            if(type=="installUpdate"){
                string installer=data.ContainsKey("installer")?Convert.ToString(data["installer"]):"";
                string sha=data.ContainsKey("sha256")?Convert.ToString(data["sha256"]):"";
                string version=data.ContainsKey("version")?Convert.ToString(data["version"]):"";string error;
                if(!UpdateLaunchPolicy.Validate(installer,Program.Data,sha,version,out error)){MessageBox.Show(this,error,"云屿",MessageBoxButtons.OK,MessageBoxIcon.Error);return;}
                string bundled=Path.Combine(root,"UpdateLauncher.exe"),updates=Path.Combine(Program.Data,"updates");Directory.CreateDirectory(updates);
                string helper=Path.Combine(updates,"UpdateLauncher-"+version+"-"+Process.GetCurrentProcess().Id+".exe");File.Copy(bundled,helper,true);
                string log=Path.Combine(updates,"update-"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+".log");
                string arguments=Q(installer)+" "+Q(sha)+" "+Q(version)+" "+Q(Path.Combine(root,"CloudDock.exe"))+" "+Q(Program.Data)+" "+Q(log)+" "+Process.GetCurrentProcess().Id;
                Process.Start(new ProcessStartInfo(helper,arguments){UseShellExecute=false,WorkingDirectory=updates,CreateNoWindow=true});
                closeToTray=false;Close();return;
            }
            if(type=="exitApplication"){RequestExit();return;}
            if(type=="startupStatus"||type=="setStartup"){
                try{
                    if(type=="setStartup" && data.ContainsKey("enabled") && data["enabled"] is bool)startupRegistration.Set((bool)data["enabled"]);
                    Send(new {type="startupState",registered=startupRegistration.Registered(),disabled=startupRegistration.DisabledByWindows()});
                }catch{Send(new {type="startupState",error="无法修改开机启动设置，请检查程序位置或 Windows 权限。"});}return;
            }
            if(type=="openWindowsStartup"){Process.Start(new ProcessStartInfo("ms-settings:startupapps"){UseShellExecute=true});return;}
            if(type=="openAuthor"){Process.Start(new ProcessStartInfo("https://space.bilibili.com/3546747290782061"){UseShellExecute=true});return;}
            if(type=="previewFeedbackSound"){
                int volume; if(!Int32.TryParse(Field(data,"volume"),out volume))return;
                PlayFeedbackSound(Field(data,"kind"),volume);return;
            }
            if(type=="dropFiles"||type=="dropUpload"){
                if(id.Length>32||e.AdditionalObjects==null||e.AdditionalObjects.Count<1||e.AdditionalObjects.Count>500)return;
                var dropped=new List<string>();
                foreach(var obj in e.AdditionalObjects){var file=obj as CoreWebView2File;
                    if(file==null||!Path.IsPathRooted(file.Path)||(!File.Exists(file.Path)&&!(type=="dropUpload"&&Directory.Exists(file.Path)))||(File.GetAttributes(file.Path)&FileAttributes.ReparsePoint)!=0){Send(new {id=id,error="请选择普通文件或真实文件夹。"});return;}
                    dropped.Add(file.Path);
                }
                var selected=await Post("api/selection",new {kind=type=="dropUpload"?"upload":"files",paths=dropped.ToArray()},true);selected["id"]=id;Send(selected);return;
            }
            if(type=="pickUploadFiles"||type=="pickUploadFolder"){
                if(picking||id.Length>32)return;picking=true;ownsPicker=true;string[] uploads=null;
                if(type=="pickUploadFiles")using(var dialog=new OpenFileDialog()){dialog.Title="选择上传文件";dialog.Multiselect=true;dialog.CheckFileExists=true;if(dialog.ShowDialog(this)==DialogResult.OK)uploads=dialog.FileNames;}
                else using(var dialog=new FolderBrowserDialog()){dialog.Description="选择一次性上传的文件夹（包含子文件夹）";if(dialog.ShowDialog(this)==DialogResult.OK)uploads=new string[]{dialog.SelectedPath};}
                if(uploads==null){Send(new {id=id,cancelled=true});return;}
                var selected=await Post("api/selection",new {kind="upload",paths=uploads},true);selected["id"]=id;Send(selected);return;
            }
            if(picking||id.Length>32||(type!="pickFiles"&&type!="pickDirectory"&&type!="pickSyncFile"))return;
            picking=true;ownsPicker=true;string[] paths=null;
            if(type=="pickFiles")using(var dialog=new OpenFileDialog()){dialog.Title="选择上传文件";dialog.Multiselect=true;dialog.CheckFileExists=true;if(dialog.ShowDialog(this)==DialogResult.OK)paths=dialog.FileNames;}
            else if(type=="pickSyncFile")using(var dialog=new SaveFileDialog()){
                dialog.Title="选择同步文件，或为云端文件选择本地保存位置";dialog.OverwritePrompt=false;dialog.CheckPathExists=true;
                if(data.ContainsKey("name")){string name=Convert.ToString(data["name"]);if(name.Length<=200&&name.IndexOfAny(Path.GetInvalidFileNameChars())<0)dialog.FileName=name;}
                if(dialog.ShowDialog(this)==DialogResult.OK)paths=new string[]{dialog.FileName};
            }
            else using(var dialog=new FolderBrowserDialog()){dialog.Description="选择本地文件夹";if(dialog.ShowDialog(this)==DialogResult.OK)paths=new string[]{dialog.SelectedPath};}
            if(paths==null){Send(new {id=id,cancelled=true});return;}
            var result=await Post("api/selection",new {kind=type=="pickDirectory"?"directory":"files",paths=paths},true);result["id"]=id;
            if(id.StartsWith("sync-",StringComparison.Ordinal)&&paths.Length==1){result["display"]=paths[0];result["name"]=Path.GetFileName(paths[0]);}Send(result);
        }catch{if(!String.IsNullOrEmpty(id))Send(new {id=id,error="文件选择未完成，请重试。"});}
        finally{if(ownsPicker)picking=false;}
    }
    void RequestExit(){if(done||closing)return;closeToTray=false;Close();}
    async void OnClosing(object sender,FormClosingEventArgs e){
        if(done)return;
        if(e.CloseReason==CloseReason.WindowsShutDown||e.CloseReason==CloseReason.TaskManagerClosing){
            closing=true;done=true;tray.Visible=false;
            try{Task.Run(async()=>{if(!String.IsNullOrEmpty(url))await Post("api/action",new {action="shutdown"});}).Wait(1500);}catch{}
            return;
        }
        e.Cancel=true;
        if(closeToTray && e.CloseReason==CloseReason.UserClosing && !closing){HideToTray();return;}
        if(closing)return;closing=true;loading.SetActive(false);Text="云屿 · 正在保存并退出…";
        try{
            if(String.IsNullOrEmpty(url) && backendTask!=null && await Task.WhenAny(backendTask,Task.Delay(1000))==backendTask){var c=await backendTask;url=Convert.ToString(c["url"]);}
            if(!String.IsNullOrEmpty(url))await Task.WhenAny(Post("api/action",new {action="shutdown"}),Task.Delay(8000));
            if(backend!=null && !backend.HasExited)await Task.WhenAny(Task.Run(()=>backend.WaitForExit()),Task.Delay(8000));
        }catch{}
        if(backend!=null && !backend.HasExited){closing=false;closeToTray=true;Text="云屿 Cloud Dock · 1.0.1 测试版";RestoreWindow(false);MessageBox.Show(this,"后台仍在保存或核对任务。请稍后再次退出；安装程序不会替换正在使用的文件。","云屿");return;}
        done=true;tray.Visible=false;Timing("exit_saved");Close();
    }
    async Task RunQa(){
        string capture=Environment.GetEnvironmentVariable("CLOUD_DOCK_QA_CAPTURE"), lifecycle=Environment.GetEnvironmentVariable("CLOUD_DOCK_QA_LIFECYCLE");
        if(String.IsNullOrEmpty(capture)&&String.IsNullOrEmpty(lifecycle))return;
        Timing("qa_started");
        // Opt-in release checks operate only on a clean test copy.
        await Task.Delay(1200);
        await EnsureService();
        var wait=Stopwatch.StartNew();
        while(wait.ElapsedMilliseconds<12000){
            if(await view.CoreWebView2.ExecuteScriptAsync("Boolean(live && !live.initializing)")=="true")break;
            await Task.Delay(200);
        }
        await Task.Delay(800);
        Timing("qa_capture_start");
        await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture))
            await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
        if(!String.IsNullOrEmpty(lifecycle)){
            await EnsureService();
            var results=new Dictionary<string,object>();results["default_close_to_tray"]=closeToTray;results["landscape_default"]=ClientSize.Width>ClientSize.Height*1.6;
            await view.CoreWebView2.ExecuteScriptAsync("changeView('backups');");await Task.Delay(200);
            results["sync_center"]=await view.CoreWebView2.ExecuteScriptAsync("Boolean(document.querySelector('#newSync') && document.querySelector('[data-new-kind=\"file\"]'))")=="true";
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".sync.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            await view.CoreWebView2.ExecuteScriptAsync("newSync('file');");await Task.Delay(200);
            results["sync_file_wizard"]=await view.CoreWebView2.ExecuteScriptAsync("Boolean(document.querySelector('#syncRemoteName') && document.querySelector('#syncKind').value==='file' && document.querySelector('#syncMode').options.length===3)")=="true";
            results["sync_preview_button_visible"]=await view.CoreWebView2.ExecuteScriptAsync("(()=>{const b=document.querySelector('#syncPreview').getBoundingClientRect();return b.bottom<innerHeight && b.top>0;})()")=="true";
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".wizard.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#sheet').close();document.body.classList.add('dark');");await Task.Delay(200);
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".dark.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.remove('dark');");
            Send(new {type="qaBridgeProbe"});var probe=Stopwatch.StartNew();while(qaBridgeReply==null&&probe.ElapsedMilliseconds<6000)await Task.Delay(50);
            results["browser_state_and_action"]=qaBridgeReply!=null && Bool(qaBridgeReply,"state",false) && Bool(qaBridgeReply,"action",false);
            results["browser_picker_route_blocked"]=qaBridgeReply!=null && Bool(qaBridgeReply,"pickerBlocked",false);
            Send(new {type="clipboardShare",provider="quark",url="https://pan.quark.cn/s/012345abcdef?pwd=Ab12",password="Ab12"});await Task.Delay(200);
            results["clipboard_prompt"]=await view.CoreWebView2.ExecuteScriptAsync("Boolean(document.querySelector('.clipboard-share:not([hidden])'))")=="true";
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".clipboard.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            results["clipboard_form_prefilled"]=await view.CoreWebView2.ExecuteScriptAsync("(()=>{document.querySelector('.clipboard-share .primary').click();return document.querySelector('#shareInput').value==='https://pan.quark.cn/s/012345abcdef?pwd=Ab12' && document.querySelector('#sharePassword').value==='Ab12';})()")=="true";
            await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#sheet').close();settings();document.querySelector('#clipboardSwitch').click();");await Task.Delay(500);
            results["release_controls"]=await view.CoreWebView2.ExecuteScriptAsync("Boolean(document.querySelector('#releaseNotes')&&document.querySelector('#exportDiagnostics'))")=="true";
            var diagnostic=await Post("api/action",new {action="diagnostics"});var diagnosticReport=(Dictionary<string,object>)diagnostic["report"];
            var packagedVersion=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"version.json")));
            results["diagnostics_allowlist"]=Convert.ToString(diagnosticReport["version"])==Convert.ToString(packagedVersion["version"])&&!json.Serialize(diagnostic).Contains("download_dir");
            results["startup_setting"]=await view.CoreWebView2.ExecuteScriptAsync("Boolean(document.querySelector('#startupSwitch') && !document.querySelector('#startupSwitch').disabled)")=="true";
            results["clipboard_setting_off"]=await view.CoreWebView2.ExecuteScriptAsync("Boolean(live.clipboard_links===false && document.querySelector('#clipboardSwitch').getAttribute('aria-checked')==='false')")=="true";
            await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#clipboardSwitch').click();");await Task.Delay(500);
            results["clipboard_setting_on"]=await view.CoreWebView2.ExecuteScriptAsync("Boolean(live.clipboard_links===true && document.querySelector('#clipboardSwitch').getAttribute('aria-checked')==='true')")=="true";
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".settings.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#exportDiagnostics').scrollIntoView({block:'center'});");await Task.Delay(100);
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".release.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            await view.CoreWebView2.ExecuteScriptAsync("historySheet({id:'qa-preview-only',enabled:true});");await Task.Delay(100);
            results["history_paused_guard"]=await view.CoreWebView2.ExecuteScriptAsync("Boolean(document.querySelector('#historyPreview').disabled && document.querySelector('#historyKeep').value==='5')")=="true";
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".history.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#sheet').close();");
            Close();await Task.Delay(150);results["close_hides_window"]=!Visible && !closing && tray.Visible;
            using(var client=new WebClient()){client.Proxy=null;results["backend_alive_in_tray"]=(await client.DownloadStringTaskAsync(new Uri(url+"health"))).Contains("true");}
            var restore=Stopwatch.StartNew();
            using(var second=Process.Start(new ProcessStartInfo(Path.Combine(root,"CloudDock.exe")){WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true})){
                while(!Visible && restore.ElapsedMilliseconds<5000)await Task.Delay(20);
                results["activation_restores_window"]=Visible && !closing;results["restore_observed_ms"]=restore.ElapsedMilliseconds;
                await Task.WhenAny(Task.Run(()=>second.WaitForExit()),Task.Delay(2000));results["second_instance_exited"]=second.HasExited;
            }
            await Post("api/action",new {action="desktop_preferences",close_to_tray=false});
            var prefs=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(Program.Data,"preferences.json")));
            results["tray_setting_persisted"]=!Bool(prefs,"close_to_tray",true);
            clipboardEnabled=true;clipboardWake=true;
            Hide();HandleClipboardText("https://pan.quark.cn/s/abcdef01?pwd=Ab12");await Task.Delay(100);
            results["clipboard_restores_tray"]=Visible&&WindowState!=FormWindowState.Minimized;
            Hide();HandleClipboardText("https://pan.quark.cn/s/abcdef01?pwd=Ab12");
            results["clipboard_duplicate_keeps_tray"]=!Visible;
            RestoreWindow(false);WindowState=FormWindowState.Minimized;
            HandleClipboardText("https://pan.baidu.com/s/1abcdef02");await Task.Delay(100);
            results["clipboard_restores_minimized"]=Visible&&WindowState!=FormWindowState.Minimized;
            clipboardWake=false;Hide();HandleClipboardText("https://pan.quark.cn/s/abcdef03");
            results["clipboard_wake_off_keeps_tray"]=!Visible;
            clipboardEnabled=false;clipboardWake=true;HandleClipboardText("https://pan.quark.cn/s/abcdef04");
            results["clipboard_disabled_keeps_tray"]=!Visible;
            clipboardEnabled=true;HandleClipboardText("ordinary text");results["ordinary_copy_keeps_tray"]=!Visible;
            RestoreWindow(false);ready=false;clipboardWake=false;
            HandleClipboardText("https://pan.quark.cn/s/abcdef05");results["clipboard_pending_before_ui"]=pendingClipboard!=null;
            HandleClipboardText("ordinary text");results["clipboard_pending_cleared"]=pendingClipboard==null;
            ready=true;clipboardWake=true;
            await Post("api/action",new {action="desktop_preferences",clipboard_wake=false});
            prefs=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(Program.Data,"preferences.json")));
            results["clipboard_wake_setting_persisted"]=!Bool(prefs,"clipboard_wake",true);
            await Post("api/action",new {action="desktop_preferences",clipboard_wake=true});
            // UI fixtures stay inside the opt-in QA process with an isolated profile.
            await view.CoreWebView2.ExecuteScriptAsync("refreshState=async()=>{}; api=async()=>({}); state.view='files';state.provider='baidu';state.path='/工作文件';state.page=0;live.providers.baidu={...live.providers.baidu,connected:true,busy:false,path:'/工作文件',page:0,context:'qa',files:[]}; live.capacities={baidu:{state:'ready',total:1e12,used:4e11,free:6e11,checked:1},quark:{state:'disconnected'}}; data.baidu['/工作文件']=[{id:'qa-dir',name:'项目资料',path:'/工作文件/项目资料',type:'folder',size:0,date:'2026-10-17'},{id:'qa-file',name:'设计说明.pdf',path:'/工作文件/设计说明.pdf',type:'pdf',size:354000,date:'2026-10-16'}];render();updateLiveControls();");
            results["capacity_visible"]=await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('[data-capacity=baidu]').textContent.includes('600.0 GB')")=="true";
            results["breadcrumbs_at_top"]=await view.CoreWebView2.ExecuteScriptAsync("Boolean(document.querySelector('.toolbar #breadcrumbs'))")=="true";
            results["folders_selectable"]=await view.CoreWebView2.ExecuteScriptAsync("(()=>{state.view='files';state.provider='baidu';state.path='/工作文件';state.page=0;live.providers.baidu={...live.providers.baidu,connected:true,busy:false,path:'/工作文件',page:0,context:'qa'};data.baidu['/工作文件']=[{id:'qa-dir',name:'项目资料',path:'/工作文件/项目资料',type:'folder',size:0,date:'2026-10-17'},{id:'qa-file',name:'设计说明.pdf',path:'/工作文件/设计说明.pdf',type:'pdf',size:354000,date:'2026-10-16'}];render();document.querySelector('#selectAll').click();return state.selected.size===2&&document.querySelectorAll('.row-check:checked').length===2&&!document.querySelector('#download').disabled})()")=="true";
            await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#clearSelection').click();document.querySelector('[data-sort-field=date]').click();document.querySelector('[data-sort-field=date]').click();");
            results["date_sort_reversible"]=await view.CoreWebView2.ExecuteScriptAsync("state.sort==='date' && state.sortDesc===true")=="true";
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".files.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            await view.CoreWebView2.ExecuteScriptAsync("live.tasks=[{id:'qa-task',provider:'baidu',name:'项目资料.zip',direction:'download',status:'completed',total:36e6,done:36e6,speed:0,target:'H:/Downloads/项目资料.zip'},{id:'qa-retry',provider:'baidu',name:'待处理.pdf',direction:'download',status:'failed',total:600000,done:0,speed:0,error:'连接中断，请重试'}];changeView('transfers');document.querySelector('[data-transfer-filter=completed]').click();");
            results["completed_tasks_compact"]=await view.CoreWebView2.ExecuteScriptAsync("document.querySelectorAll('.task').length===1 && !document.querySelector('.task .task-progress')")=="true";
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".transfers.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            await view.CoreWebView2.ExecuteScriptAsync("settings();document.querySelector('[data-settings-tab=storage]').click();");
            results["settings_categories"]=await view.CoreWebView2.ExecuteScriptAsync("document.querySelectorAll('[data-settings-tab]').length===6 && !document.querySelector('[data-settings-panel=storage]').hidden && document.querySelector('[data-settings-panel=general]').hidden")=="true";
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".storage.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#sheet').close();cloudDockCapacity('baidu');");
            results["membership_hidden"]=await view.CoreWebView2.ExecuteScriptAsync("!document.body.innerText.includes('VIP') && Boolean(document.querySelector('#capacityDetail'))")=="true";
            await Task.Delay(250);
            if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".capacity.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            string extraQa=Path.Combine(root,"tests","qa_productivity.js");
            if(File.Exists(extraQa)){
                ClientSize=new Size(980,680);await Task.Delay(250);
                await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(extraQa,Encoding.UTF8));
                var extraWait=Stopwatch.StartNew();
                while(extraWait.ElapsedMilliseconds<12000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaProductivity)")!="true")await Task.Delay(100);
                File.WriteAllText(lifecycle+".productivity.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaProductivity||{timeout:true}"));
                if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".productivity.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
            }
            string linksQa=Path.Combine(root,"tests","qa_links.js");
            if(File.Exists(linksQa)){
                await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(linksQa,Encoding.UTF8));
                var linksWait=Stopwatch.StartNew();
                while(linksWait.ElapsedMilliseconds<10000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaLinks)")!="true")await Task.Delay(100);
                File.WriteAllText(lifecycle+".links.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaLinks||{timeout:true}"));
                if(!String.IsNullOrEmpty(capture))using(var file=File.Create(capture+".download.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,file);
                await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#sheet').close();");
            }
            string feedbackQa=Path.Combine(root,"tests","qa_feedback.js");
            if(File.Exists(feedbackQa)){
                await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(feedbackQa,Encoding.UTF8));
                var feedbackWait=Stopwatch.StartNew();
                while(feedbackWait.ElapsedMilliseconds<10000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaFeedback)")!="true"){
                    if(await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaFeedbackAboutReady)")=="true"){
                        if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".author.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                        await view.CoreWebView2.ExecuteScriptAsync("window.qaFeedbackAboutReady=false");
                    }
                    await Task.Delay(50);
                }
                await Task.Delay(300);
                results["custom_sound_native_route"]=qaSoundKind=="upload"&&qaSoundVolume==22;
                var feedbackSettings=await Post("api/action",new {action="feedback_preferences",preferences=new {download_sound=false,sound_volume=18,quiet_enabled=true,quiet_start="22:00",quiet_end="08:00"}});
                var feedbackPoll=await Post("api/action",new {action="feedback_poll",ack=new string[0]});
                var fp=(Dictionary<string,object>)feedbackPoll["preferences"];
                results["native_feedback_preferences"]=!Bool(fp,"download_sound",true)&&Convert.ToInt32(fp["sound_volume"])==18&&Bool(fp,"quiet_enabled",false);
                var onDisk=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(Program.Data,"preferences.json")));
                results["feedback_settings_persisted"]=onDisk.ContainsKey("feedback");
                if(!String.IsNullOrEmpty(capture))using(var card=new FeedbackPopup("3 项下载完成","",false,()=>{}))using(var bitmap=new Bitmap(card.Width,card.Height)){
                    card.DrawToBitmap(bitmap,new Rectangle(0,0,card.Width,card.Height));bitmap.Save(capture+".desktop-notice.png",System.Drawing.Imaging.ImageFormat.Png);
                }
                File.WriteAllText(lifecycle+".feedback.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaFeedback||{timeout:true}"));
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".feedback.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#sheet').close();");
            }
            string batchQa=Path.Combine(root,"tests","qa_batch.js");
            if(File.Exists(batchQa)){
                await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(batchQa,Encoding.UTF8));
                var batchWait=Stopwatch.StartNew();
                while(batchWait.ElapsedMilliseconds<12000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaBatch)")!="true")await Task.Delay(100);
                File.WriteAllText(lifecycle+".batch.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaBatch||{timeout:true}"));
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".batch.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.add('dark')");await Task.Delay(150);
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".batch-dark.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.remove('dark');document.querySelector('#sheet').close();");
            }
            string organizeQa=Path.Combine(root,"tests","qa_organize.js");
            if(File.Exists(organizeQa)){
                await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(organizeQa,Encoding.UTF8));
                var organizeWait=Stopwatch.StartNew();
                while(organizeWait.ElapsedMilliseconds<15000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaOrganize)")!="true")await Task.Delay(100);
                File.WriteAllText(lifecycle+".organize.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaOrganize||{timeout:true}"));
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".organize.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.add('dark')");await Task.Delay(150);
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".organize-dark.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.remove('dark');document.querySelector('#sheet').close();");
            }
            string longevityQa=Path.Combine(root,"tests","qa_longevity.js");
            if(File.Exists(longevityQa)){
                await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(longevityQa,Encoding.UTF8));
                var longevityWait=Stopwatch.StartNew();
                while(longevityWait.ElapsedMilliseconds<15000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaLongevity)")!="true")await Task.Delay(100);
                File.WriteAllText(lifecycle+".longevity.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaLongevity||{timeout:true}"));
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".longevity.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#sheet').close();");
            }
            string workspaceQa=Path.Combine(root,"tests","qa_workspace.js");
            if(File.Exists(workspaceQa)){
                await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(workspaceQa,Encoding.UTF8));
                var workspaceWait=Stopwatch.StartNew();
                while(workspaceWait.ElapsedMilliseconds<12000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaWorkspace)")!="true")await Task.Delay(100);
                File.WriteAllText(lifecycle+".workspace.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaWorkspace||{timeout:true}"));
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".workspace.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.add('dark')");await Task.Delay(150);
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".workspace-dark.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.remove('dark');document.querySelector('#sheet').close();");
            }
            string usabilityQa=Path.Combine(root,"tests","qa_usability.js");
            if(File.Exists(usabilityQa)){
                await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(usabilityQa,Encoding.UTF8));
                var usabilityWait=Stopwatch.StartNew();
                while(usabilityWait.ElapsedMilliseconds<12000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaUsabilityFilesReady||window.qaUsability)")!="true")await Task.Delay(100);
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".usability-files.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("window.qaUsabilityContinue=true");
                while(usabilityWait.ElapsedMilliseconds<16000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaUsability)")!="true")await Task.Delay(100);
                File.WriteAllText(lifecycle+".usability.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaUsability||{timeout:true}"));
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".usability-settings.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.add('dark')");await Task.Delay(150);
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".usability-dark.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.remove('dark');document.querySelector('#sheet').close();");
            }
            string polishQa=Path.Combine(root,"tests","qa_polish.js");
            if(File.Exists(polishQa)){
                await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(polishQa,Encoding.UTF8));
                var polishWait=Stopwatch.StartNew();
                while(polishWait.ElapsedMilliseconds<12000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaPolishTransferReady||window.qaPolish)")!="true")await Task.Delay(100);
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".polish-transfers.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("window.qaPolishContinue=true");
                while(polishWait.ElapsedMilliseconds<16000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaPolish)")!="true")await Task.Delay(100);
                File.WriteAllText(lifecycle+".polish.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaPolish||{timeout:true}"));
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".polish-settings.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.add('dark')");await Task.Delay(150);
                if(!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".polish-dark.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                await view.CoreWebView2.ExecuteScriptAsync("document.body.classList.remove('dark');document.querySelector('#sheet').close();");
            }
            ClientSize=new Size(1280,740);
            await view.CoreWebView2.ExecuteScriptAsync("settings()");
            foreach(double factor in new double[]{1.25,1.5,2.0}){
                view.ZoomFactor=factor;await Task.Delay(250);
                results["settings_zoom_"+(int)(factor*100)]=await view.CoreWebView2.ExecuteScriptAsync("(()=>{const d=document.querySelector('#sheet'),p=document.querySelector('.settings-panels'),b=document.querySelector('#closeSheet').getBoundingClientRect();return document.documentElement.scrollWidth<=innerWidth+1&&p.scrollWidth<=p.clientWidth+1&&b.right<=innerWidth&&b.bottom<=innerHeight&&d.open})()")=="true";
                if(factor==2.0&&!String.IsNullOrEmpty(capture))using(var image=File.Create(capture+".polish-zoom200.png"))await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
            }
            view.ZoomFactor=1.0;await view.CoreWebView2.ExecuteScriptAsync("document.querySelector('#sheet').close()");
            if(File.Exists(Path.Combine(root,"tests","qa_stability.js")))await RunStabilityQa(lifecycle);
            File.WriteAllText(lifecycle,json.Serialize(results));
        }
        RequestExit();
    }
    async Task RunStabilityQa(string file){
        await view.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(Path.Combine(root,"tests","qa_stability.js"),Encoding.UTF8));
        var wait=Stopwatch.StartNew();
        while(wait.ElapsedMilliseconds<25000 && await view.CoreWebView2.ExecuteScriptAsync("Boolean(window.qaStability)")!="true")await Task.Delay(100);
        File.WriteAllText(file+".stability.json",await view.CoreWebView2.ExecuteScriptAsync("window.qaStability||{timeout:true}"));
        var result=new Dictionary<string,object>();
        int backendId=backend.Id;
        foreach(string method in new[]{"Page.crash","Browser.crash"}){
            await view.CoreWebView2.ExecuteScriptAsync("window.chrome.webview.postMessage({type:'uiLocation',view:'transfers',provider:'baidu'})");
            await Task.Delay(150);int before=readyCount;
            var crash=view.CoreWebView2.CallDevToolsProtocolMethodAsync(method,"{}");
            // A crashed process cannot return a CDP result; observe any task fault.
            var observeCrash=crash.ContinueWith(t=>{var ignored=t.Exception;},TaskContinuationOptions.OnlyOnFaulted);
            await Task.WhenAny(crash,Task.Delay(1000));
            wait.Restart();while(wait.ElapsedMilliseconds<20000 && (!ready || readyCount<=before))await Task.Delay(100);
            result[method+"_recovered"]=ready && readyCount>before;
            result[method+"_backend_retained"]=backend.Id==backendId && !backend.HasExited;
            if(!ready || readyCount<=before)break;
            await Task.Delay(1200);
            result[method+"_location_retained"]=await view.CoreWebView2.ExecuteScriptAsync("state.view==='transfers'")=="true";
        }
        File.WriteAllText(file+".recovery.json",json.Serialize(result));
    }
}
