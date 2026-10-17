using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Security.Cryptography;

static class RuntimePaths {
    public static string Data(string root){
        string value=Environment.GetEnvironmentVariable("CLOUD_DOCK_DATA_DIR");
        string marker=Path.Combine(root,"installed-data.txt");
        if(String.IsNullOrWhiteSpace(value)&&File.Exists(marker))value=File.ReadAllText(marker,Encoding.UTF8).Trim();
        if(String.IsNullOrWhiteSpace(value))value=Path.Combine(root,"data");
        if(!Path.IsPathRooted(value)||value.IndexOfAny(new char[]{'\r','\n','\0','"'})>=0)throw new IOException("Invalid data directory");
        return Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar);
    }
    public static string Key(string path){using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(path.ToLowerInvariant()))).Replace("-","");}
    public static void Prepare(string data){
        string maintenance=data+".installing";
        if(File.Exists(maintenance)){int pid;if(Int32.TryParse(File.ReadAllText(maintenance).Trim(),out pid)){try{var running=System.Diagnostics.Process.GetProcessById(pid);if(!running.HasExited)throw new IOException("Setup is in progress");}catch(ArgumentException){}}}
        Directory.CreateDirectory(data);
        for(var p=new DirectoryInfo(data);p!=null;p=p.Parent)
            if((p.Attributes&FileAttributes.ReparsePoint)!=0)throw new IOException("Linked data directory is unsupported");
        Directory.CreateDirectory(Path.Combine(data,"work"));
    }
    public static bool Busy(string data,string legacyRoot){
        Mutex m;
        foreach(string name in new string[]{"Local\\CloudDock-"+Key(data),"Local\\CloudDock-"+Key(legacyRoot.EndsWith("\\")?legacyRoot:legacyRoot+"\\")})
            if(Mutex.TryOpenExisting(name,out m)){m.Dispose();return true;}
        foreach(string file in new string[]{".app-lock",".backend-lock"}){
            string path=Path.Combine(data,file);
            if(File.Exists(path))try{using(var f=new FileStream(path,FileMode.Open,FileAccess.ReadWrite,FileShare.None)){} }catch(IOException){return true;}
        }
        // Older portable releases did not hold the backend lock. Do not import or
        // replace their runtime while a Python process from that checkout survives.
        string runtime=Path.Combine(legacyRoot,"runtime","python","python.exe"),venv=Path.Combine(legacyRoot,".venv","Scripts","python.exe");
        foreach(var process in System.Diagnostics.Process.GetProcessesByName("python"))try{
            string path=process.MainModule.FileName;
            if(String.Equals(path,runtime,StringComparison.OrdinalIgnoreCase)||String.Equals(path,venv,StringComparison.OrdinalIgnoreCase))return true;
        }catch{}finally{process.Dispose();}
        return false;
    }
    public static int RequestExit(string data,string legacyRoot){
        EventWaitHandle ev;
        if(EventWaitHandle.TryOpenExisting("Local\\CloudDock-Exit-"+Key(data),out ev)){ev.Set();ev.Dispose();}
        for(int i=0;i<150;i++){if(!Busy(data,legacyRoot))return 0;Thread.Sleep(200);}
        return 3;
    }
}
