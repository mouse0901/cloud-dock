using System;
using System.IO;
using Microsoft.Win32;

sealed class StartupRegistration {
    const string RunPath=@"Software\Microsoft\Windows\CurrentVersion\Run";
    readonly string executable,valueName;
    public StartupRegistration(string path,string name="CloudDock"){executable=Path.GetFullPath(path);valueName=name;}
    public static string Command(string path){
        if(String.IsNullOrEmpty(path)||path.IndexOfAny(new char[]{'"','\r','\n','\0'})>=0||!Path.IsPathRooted(path))throw new ArgumentException("Invalid application path");
        string command="\""+Path.GetFullPath(path)+"\" --startup";
        if(command.Length>260)throw new ArgumentException("Application path is too long for startup");return command;
    }
    public bool Registered(){using(var key=Registry.CurrentUser.OpenSubKey(RunPath))return key!=null && String.Equals(Convert.ToString(key.GetValue(valueName,"")),Command(executable),StringComparison.OrdinalIgnoreCase);}
    public bool DisabledByWindows(){
        using(var key=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run")){
            var value=key==null?null:key.GetValue(valueName) as byte[];
            return value!=null && value.Length>0 && (value[0]==3||value[0]==7);
        }
    }
    public void Set(bool enabled){
        using(var key=Registry.CurrentUser.CreateSubKey(RunPath)){
            if(enabled){if(!File.Exists(executable))throw new IOException();key.SetValue(valueName,Command(executable),RegistryValueKind.String);}
            else if(Registered())key.DeleteValue(valueName,false);
        }
    }
}
