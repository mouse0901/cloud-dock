using System;
using System.IO;
class RecoveryFailedInstaller {
 static int Main(){string root=new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory).Parent.Parent.FullName;if(!Path.GetFileName(root).StartsWith("recovery-flow-"))return 24;File.WriteAllText(Path.Combine(root,"app","CloudDock.exe"),"partially installed invalid binary");return 23;}
}
