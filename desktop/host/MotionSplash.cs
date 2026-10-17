using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Diagnostics;
using System.Windows.Forms;

// Native first-frame animation; independent of Python, WebView2 and network state.
sealed class MotionSplash:Control {
    readonly Timer timer=new Timer();readonly Stopwatch clock=Stopwatch.StartNew();
    readonly Bitmap logo;readonly Button retry=new Button(),exit=new Button();
    readonly Font title=new Font("Microsoft YaHei UI",18,FontStyle.Bold);
    readonly Font sub=new Font("Segoe UI",8,FontStyle.Regular);
    readonly Font statusFont=new Font("Microsoft YaHei UI",9,FontStyle.Regular);
    bool dark,reduced,active=true,firstPaint,failure;
    string status="正在打开工作空间";
    public event EventHandler Retry,ExitRequested,FirstPaint;
    public MotionSplash(string icon){
        SetStyle(ControlStyles.AllPaintingInWmPaint|ControlStyles.UserPaint|ControlStyles.OptimizedDoubleBuffer|ControlStyles.ResizeRedraw,true);
        using(var source=Image.FromFile(System.IO.Path.ChangeExtension(icon,".png")))logo=new Bitmap(source);
        AccessibleName="云屿正在打开工作空间";AccessibleRole=AccessibleRole.StaticText;
        retry.Text="重试";exit.Text="退出云屿";retry.FlatStyle=exit.FlatStyle=FlatStyle.Flat;
        retry.Size=new Size(80,32);exit.Size=new Size(96,32);retry.Visible=exit.Visible=false;
        retry.Click+=(s,e)=>{if(Retry!=null)Retry(this,EventArgs.Empty);};
        exit.Click+=(s,e)=>{if(ExitRequested!=null)ExitRequested(this,EventArgs.Empty);};
        Controls.Add(retry);Controls.Add(exit);timer.Interval=33;timer.Tick+=(s,e)=>Invalidate();
        VisibleChanged+=(s,e)=>UpdateTimer();timer.Start();
    }
    public void SetAppearance(bool isDark,bool reduce){dark=isDark;reduced=reduce;BackColor=dark?Color.FromArgb(19,23,30):Color.FromArgb(248,249,252);ForeColor=dark?Color.FromArgb(237,241,248):Color.FromArgb(36,43,56);retry.BackColor=exit.BackColor=BackColor;retry.ForeColor=exit.ForeColor=ForeColor;UpdateTimer();Invalidate();}
    public void SetActive(bool value){active=value;UpdateTimer();}
    void UpdateTimer(){timer.Enabled=active && Visible && !reduced && !failure;}
    public void Prepare(){failure=false;status="正在打开工作空间";retry.Visible=exit.Visible=false;clock.Restart();active=true;UpdateTimer();Invalidate();}
    public void ShowFailure(string message,bool allowRetry){failure=true;status=message;AccessibleName=message;retry.Visible=allowRetry;exit.Visible=true;UpdateTimer();Invalidate();}
    protected override void OnResize(EventArgs e){base.OnResize(e);retry.Location=new Point(Width/2-94,Height/2+125);exit.Location=new Point(Width/2+1,Height/2+125);}
    protected override void OnPaint(PaintEventArgs e){
        base.OnPaint(e);if(!firstPaint){firstPaint=true;if(FirstPaint!=null)FirstPaint(this,EventArgs.Empty);}
        var g=e.Graphics;g.SmoothingMode=SmoothingMode.AntiAlias;g.InterpolationMode=InterpolationMode.HighQualityBicubic;g.PixelOffsetMode=PixelOffsetMode.HighQuality;
        float dpi=g.DpiX/96f,cx=Width/2f,cy=Height/2f-61*dpi;
        double t=clock.Elapsed.TotalSeconds;
        float lift=reduced||failure?0:(float)Math.Sin(Math.Max(0,t-1)*Math.PI/1.8)*1.8f*dpi;
        if(!reduced&&!failure){
            using(var path=new GraphicsPath()){
                path.AddEllipse(cx-84*dpi,cy-84*dpi,168*dpi,168*dpi);
                using(var halo=new PathGradientBrush(path)){halo.CenterColor=Color.FromArgb(dark?24:16,39,170,245);halo.SurroundColors=new[]{Color.Transparent};g.FillPath(halo,path);}
            }
            if(t<1.05){
                double p=Math.Min(1,t/1.05),ease=1-Math.Pow(1-p,3);
                for(int side=0;side<2;side++)for(int tail=5;tail>=0;tail--){
                    double angle=(-115+230*ease+180*side-tail*5)*Math.PI/180;
                    float radius=(float)(98*(1-ease)+18*ease)*dpi;
                    float diameter=(9-tail*.65f)*dpi;
                    int alpha=(int)(Math.Sin(Math.PI*p)*(160-tail*21));
                    using(var dot=new SolidBrush(Color.FromArgb(Math.Max(0,alpha),side==0?Color.FromArgb(25,189,242):Color.FromArgb(47,217,165))))
                        g.FillEllipse(dot,cx+(float)Math.Cos(angle)*radius-diameter/2,cy+(float)Math.Sin(angle)*radius-diameter/2,diameter,diameter);
                }
            }
        }
        float scale=reduced||failure?1:(float)(.72+.28*(1-Math.Pow(1-Math.Min(1,t/.7),3)));
        float size=78*dpi*scale;
        g.DrawImage(logo,cx-size/2,cy-size/2+lift,size,size);
        using(var ink=new SolidBrush(ForeColor))using(var muted=new SolidBrush(dark?Color.FromArgb(163,175,193):Color.FromArgb(109,119,135)))using(var center=new StringFormat{Alignment=StringAlignment.Center,LineAlignment=StringAlignment.Center}){
            g.DrawString("云屿",title,ink,new RectangleF(0,cy+55*dpi,Width,35*dpi),center);
            g.DrawString("C L O U D   D O C K",sub,muted,new RectangleF(0,cy+91*dpi,Width,18*dpi),center);
            g.DrawString(status,statusFont,muted,new RectangleF(20,cy+134*dpi,Width-40,40*dpi),center);
        }
        if(!reduced&&!failure){
            float left=cx-28*dpi,y=cy+187*dpi;
            using(var track=new SolidBrush(dark?Color.FromArgb(45,53,66):Color.FromArgb(227,231,239)))g.FillRectangle(track,left,y,56*dpi,2*dpi);
            double sweep=(1-Math.Cos(t*Math.PI/0.8))/2;
            using(var accent=new SolidBrush(dark?Color.FromArgb(123,165,255):Color.FromArgb(27,98,232)))g.FillRectangle(accent,left+(float)sweep*35*dpi,y,21*dpi,2*dpi);
        }
    }
    protected override void Dispose(bool disposing){if(disposing){timer.Dispose();logo.Dispose();title.Dispose();sub.Dispose();statusFont.Dispose();}base.Dispose(disposing);}
}
