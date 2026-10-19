using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

// Only files in the installed distribution manifest are backed up. User data is never copied.
static class UpdateRecovery {
    static JavaScriptSerializer Json=new JavaScriptSerializer(){MaxJsonLength=4*1024*1024};
    public static string Hash(string path){using(var f=File.OpenRead(path))using(var h=SHA256.Create())return BitConverter.ToString(h.ComputeHash(f)).Replace("-","").ToLowerInvariant();}
    public static string Safe(string root,string relative){
        if(String.IsNullOrEmpty(relative)||Path.IsPathRooted(relative)||relative.Contains(":")||relative.Split('/','\\').Any(x=>x==".."||x=="."||x==""))throw new IOException("Invalid relative path");
        string dir=Path.GetFullPath(root).TrimEnd('\\','/'),file=Path.GetFullPath(Path.Combine(dir,relative));
        if(!file.StartsWith(dir+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new IOException("Outside directory");
        for(string p=file;p!=null;p=Path.GetDirectoryName(p)){
            if((File.Exists(p)||Directory.Exists(p))&&(File.GetAttributes(p)&FileAttributes.ReparsePoint)!=0)throw new IOException("Linked path");
            if(String.Equals(p,dir,StringComparison.OrdinalIgnoreCase))break;
        }
        return file;
    }
    public static Dictionary<string,string> Files(string root){
        var result=new Dictionary<string,string>(StringComparer.OrdinalIgnoreCase);string manifest=Safe(root,"CHECKSUMS.sha256");
        if(!File.Exists(manifest)||new FileInfo(manifest).Length>1024*1024)throw new IOException("Missing distribution manifest");
        foreach(string line in File.ReadAllLines(manifest,Encoding.UTF8)){
            if(String.IsNullOrWhiteSpace(line))continue;
            var match=Regex.Match(line,@"^([a-f0-9]{64})  (.+)$");if(!match.Success)throw new IOException("Invalid distribution manifest");
            string rel=match.Groups[2].Value.Replace('\\','/');Safe(root,rel);
            if(rel.Equals("installed-data.txt",StringComparison.OrdinalIgnoreCase)||rel.StartsWith("data/",StringComparison.OrdinalIgnoreCase)||rel.StartsWith("downloads/",StringComparison.OrdinalIgnoreCase)||rel.StartsWith("work/",StringComparison.OrdinalIgnoreCase)||result.ContainsKey(rel))throw new IOException("Invalid distribution file");
            result.Add(rel,match.Groups[1].Value);
        }
        if(result.Count>10000||!result.ContainsKey("CloudDock.exe")||!result.ContainsKey("version.json"))throw new IOException("Incomplete distribution manifest");
        return result;
    }
    public static string DirectoryFor(string data){return Path.Combine(data,"updates","recovery");}
    public static string StatePath(string data){return Path.Combine(DirectoryFor(data),"state.json");}
    public static Dictionary<string,object> Read(string data){
        string file=StatePath(data);if(!File.Exists(file)||new FileInfo(file).Length>4*1024*1024)return null;
        return Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(file,Encoding.UTF8));
    }
    static void Write(string data,Dictionary<string,object> state){
        Directory.CreateDirectory(DirectoryFor(data));string file=StatePath(data),tmp=file+"."+Guid.NewGuid().ToString("N")+".tmp";
        File.WriteAllText(tmp,Json.Serialize(state),new UTF8Encoding(false));if(File.Exists(file))File.Replace(tmp,file,null);else File.Move(tmp,file);
    }
    public static void Status(string data,string status){var s=Read(data);if(s==null)return;s["status"]=status;s["changed_utc"]=DateTime.UtcNow.ToString("o");Write(data,s);}
    public static string Prepare(string appRoot,string data,string version){
        Dictionary<string,object> previous=null;try{previous=Read(data);}catch{}
        string root=Path.GetFullPath(appRoot).TrimEnd('\\','/'),dataFull=Path.GetFullPath(data).TrimEnd('\\','/');
        if(dataFull.StartsWith(root+"\\",StringComparison.OrdinalIgnoreCase)||root.StartsWith(dataFull+"\\",StringComparison.OrdinalIgnoreCase)||String.Equals(root,dataFull,StringComparison.OrdinalIgnoreCase))throw new IOException("Data must be separate from application");
        var files=Files(root);files["CHECKSUMS.sha256"]=Hash(Safe(root,"CHECKSUMS.sha256"));
        long size=0;foreach(var pair in files){var path=Safe(root,pair.Key);if(!File.Exists(path)||Hash(path)!=pair.Value)throw new IOException("Application files changed");size+=new FileInfo(path).Length;}
        string folder=DirectoryFor(data);Directory.CreateDirectory(folder);if(new DriveInfo(Path.GetPathRoot(folder)).AvailableFreeSpace<size+64*1024*1024)throw new IOException("Not enough recovery space");
        string id=Guid.NewGuid().ToString("N"),backup=Safe(folder,id);Directory.CreateDirectory(backup);
        foreach(var pair in files){string target=Safe(backup,pair.Key);Directory.CreateDirectory(Path.GetDirectoryName(target));File.Copy(Safe(root,pair.Key),target,false);if(Hash(target)!=pair.Value)throw new IOException("Recovery verification failed");}
        string from="";try{from=Convert.ToString(Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"version.json")))["version"]);}catch{}
        Write(data,new Dictionary<string,object>{{"schema",1},{"id",id},{"root",root},{"from",from},{"to",version},{"files",files},{"status","prepared"},{"changed_utc",DateTime.UtcNow.ToString("o")}});
        RetirePrevious(data,previous,id);return id;
    }
    static void RetirePrevious(string data,Dictionary<string,object> previous,string current){
        // Only a verified prior snapshot named in our previous state is eligible.
        // Unknown files and damaged snapshots are left for explicit inspection.
        try{
            if(previous==null)return;string id=Convert.ToString(previous["id"]);
            if(id==current||!Regex.IsMatch(id,@"^[a-f0-9]{32}$"))return;
            string backup=Safe(DirectoryFor(data),id);var files=(Dictionary<string,object>)previous["files"];
            if(files.Count>10001||!files.ContainsKey("CloudDock.exe"))return;
            var directories=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach(var pair in files){string file=Safe(backup,pair.Key);if(Hash(file)!=Convert.ToString(pair.Value))return;for(string p=Path.GetDirectoryName(file);p!=null&&p.Length>=backup.Length;p=Path.GetDirectoryName(p))directories.Add(p);}
            foreach(var pair in files)File.Delete(Safe(backup,pair.Key));
            foreach(string dir in directories.OrderByDescending(x=>x.Length))if(!Directory.EnumerateFileSystemEntries(dir).Any())Directory.Delete(dir,false);
        }catch{}
    }
    public static void Restore(string root,string data){
        var s=Read(data);if(s==null||Convert.ToInt32(s["schema"])!=1||!String.Equals(Path.GetFullPath(root).TrimEnd('\\','/'),Convert.ToString(s["root"]),StringComparison.OrdinalIgnoreCase))throw new IOException("Recovery root mismatch");
        string id=Convert.ToString(s["id"]);if(!Regex.IsMatch(id,@"^[a-f0-9]{32}$"))throw new IOException("Recovery id invalid");
        string backup=Safe(DirectoryFor(data),id);var files=(Dictionary<string,object>)s["files"];
        if(files.Count>10001||!files.ContainsKey("CloudDock.exe"))throw new IOException("Recovery manifest invalid");
        foreach(var pair in files){string sha=Convert.ToString(pair.Value);if(!Regex.IsMatch(sha,@"^[a-f0-9]{64}$")||Hash(Safe(backup,pair.Key))!=sha)throw new IOException("Recovery file changed");Safe(root,pair.Key);}
        Status(data,"restoring");
        foreach(var pair in files){string target=Safe(root,pair.Key);Directory.CreateDirectory(Path.GetDirectoryName(target));string tmp=target+".recovery-"+Guid.NewGuid().ToString("N");File.Copy(Safe(backup,pair.Key),tmp,false);try{if(File.Exists(target))File.Replace(tmp,target,null);else File.Move(tmp,target);}finally{if(File.Exists(tmp))File.Delete(tmp);}if(Hash(target)!=Convert.ToString(pair.Value))throw new IOException("Restore verification failed");}
        Status(data,"restored");
    }
    public static object Public(string data){
        try{var s=Read(data);return s==null?new {available=false,status="none",from="",to=""}: (object)new {available=true,status=Convert.ToString(s["status"]),from=Convert.ToString(s["from"]),to=Convert.ToString(s["to"])};}catch{return new {available=false,status="invalid",from="",to=""};}
    }
    public static void Healthy(string data,string root){
        try{var s=Read(data);if(s==null||!String.Equals(Path.GetFullPath(root).TrimEnd('\\','/'),Convert.ToString(s["root"]),StringComparison.OrdinalIgnoreCase))return;var version=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"version.json")));if(Convert.ToString(version["version"])==Convert.ToString(s["to"])&&new[]{"installed","waiting_health"}.Contains(Convert.ToString(s["status"])))Status(data,"healthy");}catch{}
    }
}
