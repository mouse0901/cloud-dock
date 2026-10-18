using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Media;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

partial class CloudWindow {
    readonly System.Windows.Forms.Timer feedbackTimer=new System.Windows.Forms.Timer();
    readonly HashSet<string> feedbackSeen=new HashSet<string>();
    readonly Queue<string> feedbackOrder=new Queue<string>();
    string[] feedbackAck=new string[0];
    bool feedbackPolling;int soundPlaying;
    FeedbackPopup feedbackPopup;
    Dictionary<string,object> pendingFeedback;
    string qaSoundKind="";int qaSoundVolume;
    [DllImport("shell32.dll")] static extern int SHQueryUserNotificationState(out int state);

    void InitializeFeedback(){
        feedbackTimer.Interval=1500;
        feedbackTimer.Tick+=async(s,e)=>await PollFeedback();feedbackTimer.Start();
        FormClosed+=(s,e)=>{feedbackTimer.Stop();feedbackTimer.Dispose();if(feedbackPopup!=null&&!feedbackPopup.IsDisposed)feedbackPopup.Close();};
    }
    static bool SystemQuiet(){try{int state;return SHQueryUserNotificationState(out state)==0 && state!=5;}catch{return false;}}
    static string Field(Dictionary<string,object> row,string name){return row.ContainsKey(name)?Convert.ToString(row[name]):"";}
    async Task PollFeedback(){
        if(feedbackPolling||closing||(backendTask==null&&String.IsNullOrEmpty(url)))return;
        feedbackPolling=true;
        try{
            var response=await Post("api/action",new {action="feedback_poll",ack=feedbackAck});
            var settings=response.ContainsKey("preferences")?response["preferences"] as Dictionary<string,object>:null;
            var events=response.ContainsKey("events")?response["events"] as IEnumerable:null;
            if(settings==null||events==null)return;
            bool silent=Bool(response,"quiet",false)||SystemQuiet();
            bool foreground=Visible&&WindowState!=FormWindowState.Minimized&&GetForegroundWindow()==Handle;
            var ack=new List<string>();var arrivals=new List<Dictionary<string,object>>();
            foreach(var entry in events){
                var row=entry as Dictionary<string,object>;if(row==null)continue;
                string id=Field(row,"id");if(id.Length!=24)continue;ack.Add(id);
                if(!feedbackSeen.Add(id))continue;
                feedbackOrder.Enqueue(id);while(feedbackOrder.Count>500)feedbackSeen.Remove(feedbackOrder.Dequeue());
                if(!silent)arrivals.Add(row);
            }
            feedbackAck=ack.ToArray();
            if(closing||IsDisposed||arrivals.Count==0)return;
            Dictionary<string,object> audible=null;var visible=new List<Dictionary<string,object>>();
            foreach(var row in arrivals){
                if(Bool(row,"notice",false))visible.Add(row);
                if(Bool(row,"audible",false)&&(audible==null||Field(row,"kind")=="error"))audible=row;
            }
            if(audible!=null&&!picking)PlayFeedbackSound(Field(audible,"kind"),Convert.ToInt32(settings["sound_volume"]));
            if(visible.Count==0)return;
            var shown=visible[visible.Count-1];
            if(visible.Count>1){shown=new Dictionary<string,object>(shown);shown["title"]="有 "+visible.Count+" 条新的传输或账号通知";shown["view"]="notifications";shown["task"]="";shown.Remove("name");}
            if(foreground&&ready){Send(new {type="feedbackEvent",item=shown});}
            else if(Bool(settings,"desktop_notifications",true)){
                if(feedbackPopup!=null&&!feedbackPopup.IsDisposed)feedbackPopup.Close();
                var item=shown;feedbackPopup=new FeedbackPopup(Field(item,"title"),Field(item,"name"),dark,()=>ActivateFeedback(item));
                feedbackPopup.Show();
            }
        }catch{ /* Backend restarts retry on the next timer without losing acknowledgement. */ }
        finally{feedbackPolling=false;}
    }
    void ActivateFeedback(Dictionary<string,object> item){
        pendingFeedback=item;RestoreWindow(false);
        if(ready){Send(new {type="feedbackOpen",item=pendingFeedback});pendingFeedback=null;}
    }
    void PlayFeedbackSound(string kind,int volume){
        if(kind!="download"&&kind!="upload"&&kind!="error")return;
        volume=Math.Max(0,Math.Min(100,volume));if(volume==0||Interlocked.CompareExchange(ref soundPlaying,1,0)!=0)return;
        // Explicit QA runs inspect the waveform and route without making the user's PC beep.
        if(!String.IsNullOrEmpty(Environment.GetEnvironmentVariable("CLOUD_DOCK_QA_LIFECYCLE"))){qaSoundKind=kind;qaSoundVolume=volume;soundPlaying=0;return;}
        Task.Run(()=>{
            try{
                byte[] data=File.ReadAllBytes(Path.Combine(root,"desktop","sounds",kind+".wav"));
                if(data.Length<44||data.Length>200000||data[0]!='R'||data[8]!='W')return;
                for(int i=44;i+1<data.Length;i+=2){short sample=(short)(data[i]|data[i+1]<<8);sample=(short)(sample*volume/100);data[i]=(byte)sample;data[i+1]=(byte)(sample>>8);}
                using(var stream=new MemoryStream(data))using(var player=new SoundPlayer(stream)){player.Load();player.PlaySync();}
            }catch{}finally{Interlocked.Exchange(ref soundPlaying,0);}
        });
    }
}

// An application-owned, silent desktop card. It never activates over the current app.
sealed class FeedbackPopup:Form {
    readonly System.Windows.Forms.Timer expiry=new System.Windows.Forms.Timer();
    protected override bool ShowWithoutActivation{get{return true;}}
    protected override CreateParams CreateParams{get{var p=base.CreateParams;p.ExStyle|=0x08000000|0x80;return p;}}
    public FeedbackPopup(string title,string filename,bool dark,Action open){
        FormBorderStyle=FormBorderStyle.None;ShowInTaskbar=false;TopMost=true;AutoScaleMode=AutoScaleMode.Dpi;
        Size=new Size(360,140);StartPosition=FormStartPosition.Manual;
        var screen=Screen.FromPoint(Cursor.Position).WorkingArea;Location=new Point(screen.Right-Width-18,screen.Bottom-Height-18);
        BackColor=dark?Color.FromArgb(39,40,44):Color.FromArgb(248,250,254);ForeColor=dark?Color.White:Color.FromArgb(32,36,44);
        Font=new Font("Microsoft YaHei UI",9);
        var brand=new Label{Text="云屿",Left=18,Top=13,Width=280,Height=23};Controls.Add(brand);
        var close=new Button{Text="×",Left=320,Top=6,Width=32,Height=28,FlatStyle=FlatStyle.Flat,TabStop=false};close.FlatAppearance.BorderSize=0;close.Click+=(s,e)=>Close();Controls.Add(close);
        var text=new Label{Text=title+(filename.Length>0?"\n"+filename:""),Left=18,Top=42,Width=320,Height=48,AutoEllipsis=true};Controls.Add(text);
        var action=new Button{Text="查看详情",Left=16,Top=96,Width=100,Height=30,FlatStyle=FlatStyle.Flat};action.FlatAppearance.BorderSize=0;action.ForeColor=Color.FromArgb(62,138,239);action.Click+=(s,e)=>{Close();open();};Controls.Add(action);
        expiry.Interval=8000;expiry.Tick+=(s,e)=>{if(!Bounds.Contains(Cursor.Position))Close();};Shown+=(s,e)=>expiry.Start();FormClosed+=(s,e)=>{expiry.Stop();expiry.Dispose();};
    }
}
