using System;
using System.Drawing;
using System.Reflection;
using System.Windows.Forms;
using System.Threading;
using System.IO;
class FeedbackPopupProbe {
 [STAThread] static int Main(string[] args){
  Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
  var assembly=Assembly.LoadFrom(Path.Combine(args[0],"CloudDock.exe"));
  var type=assembly.GetType("FeedbackPopup",true);
  using(var popup=(Form)Activator.CreateInstance(type,new object[]{"3 项下载完成","",false,(Action)(()=>{})})){
   popup.Show();for(int i=0;i<12;i++){Application.DoEvents();Thread.Sleep(25);}
   using(var bmp=new Bitmap(popup.Width,popup.Height)){
    popup.DrawToBitmap(bmp,new Rectangle(0,0,bmp.Width,bmp.Height));bmp.Save(args[1],System.Drawing.Imaging.ImageFormat.Png);
    int contrast=0;for(int y=0;y<bmp.Height;y++)for(int x=0;x<bmp.Width;x++){var c=bmp.GetPixel(x,y);if(c.R<150&&c.G<150&&c.B<150)contrast++;}
    Console.WriteLine("visible="+popup.Visible+" controls="+popup.Controls.Count+" text_pixels="+contrast);
    popup.Close();return contrast>200?0:1;
   }
  }
 }
}
