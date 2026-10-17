using System;
using System.IO;
using System.Security.Cryptography;
using System.Text.RegularExpressions;

static class UpdateLaunchPolicy {
    public static bool Validate(string installer,string data,string sha256,string version,out string error){
        error="";
        try{
            if(!Regex.IsMatch(version??"",@"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")||
               !Regex.IsMatch(sha256??"",@"^[a-f0-9]{64}$")){error="更新参数无效。";return false;}
            string file=Path.GetFullPath(installer),folder=Path.GetFullPath(Path.Combine(data,"updates"))+Path.DirectorySeparatorChar;
            if(!file.StartsWith(folder,StringComparison.OrdinalIgnoreCase)||Path.GetFileName(file)!="CloudDock-"+version+"-Setup-x64.exe"||!File.Exists(file)){error="更新包位置无效。";return false;}
            using(var input=File.OpenRead(file))using(var hash=SHA256.Create()){
                string actual=BitConverter.ToString(hash.ComputeHash(input)).Replace("-","").ToLowerInvariant();
                if(!String.Equals(actual,sha256,StringComparison.Ordinal)){error="更新包校验失败。";return false;}
            }
            return true;
        }catch{error="无法验证更新包。";return false;}
    }
}
