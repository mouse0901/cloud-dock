using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Linq;

class UpdateLauncher {
    static string Quote(string value){return "\""+(value??"").Replace("\"","\\\"")+"\"";}
    static void Log(string path,string message){
        try{File.AppendAllText(path,DateTime.UtcNow.ToString("o")+" "+message+Environment.NewLine,new UTF8Encoding(false));}catch{}
    }
    static int Main(string[] args){
        if(args.Length==4&&args[0]=="--recover"){
            int pid;if(!Int32.TryParse(args[3],out pid))return 2;
            try{Wait(pid);UpdateRecovery.Restore(Path.GetDirectoryName(args[1]),args[2]);Restart(args[1]);return 0;}catch{try{UpdateRecovery.Status(args[2],"restore_failed");}catch{}Restart(args[1]);return 9;}
        }
        if(args.Length!=7)return 2;
        string installer=args[0],sha=args[1],version=args[2],app=args[3],data=args[4],log=args[5];int parent;
        if(!Int32.TryParse(args[6],out parent))return 2;
        string error;
        if(!UpdateLaunchPolicy.Validate(installer,data,sha,version,out error)){Log(log,error);return 3;}
        bool prepared=false;
        try{
            Wait(parent);
            if(!UpdateLaunchPolicy.Validate(installer,data,sha,version,out error))throw new IOException("Installer changed");
            UpdateRecovery.Prepare(Path.GetDirectoryName(app),data,version);
            prepared=true;
            UpdateRecovery.Status(data,"installing");
            var setup=new ProcessStartInfo(installer,"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE=1 /LOG="+Quote(log));
            setup.UseShellExecute=false;setup.WorkingDirectory=Path.GetDirectoryName(installer);
            using(var process=Process.Start(setup)){
                if(!process.WaitForExit(300000)){UpdateRecovery.Status(data,"installation_uncertain");Log(log,"安装程序仍在运行，未强制恢复或启动其他实例。");return 5;}
                if(process.ExitCode!=0){Log(log,"安装未完成，退出码 "+process.ExitCode);UpdateRecovery.Restore(Path.GetDirectoryName(app),data);Restart(app);return 6;}
            }
            if(!File.Exists(app)){UpdateRecovery.Restore(Path.GetDirectoryName(app),data);Restart(app);return 7;}
            UpdateRecovery.Status(data,"installed");Log(log,"更新至 "+version+"，等待启动确认。");
            using(var started=Process.Start(new ProcessStartInfo(app){UseShellExecute=true,WorkingDirectory=Path.GetDirectoryName(app)})){
                for(int i=0;i<90;i++){
                    var state=UpdateRecovery.Read(data);if(state!=null&&Convert.ToString(state["status"])=="healthy")return 0;
                    if(started.HasExited){UpdateRecovery.Restore(Path.GetDirectoryName(app),data);Restart(app);return 10;}
                    System.Threading.Thread.Sleep(500);
                }
            }
            UpdateRecovery.Status(data,"waiting_health");return 0;
        }catch(Exception error2){Log(log,"更新启动失败："+error2.GetType().Name);try{var s=UpdateRecovery.Read(data);if(prepared&&s!=null&&Convert.ToString(s["to"])==version&&new[]{"installing","installed","restoring"}.Contains(Convert.ToString(s["status"])))UpdateRecovery.Restore(Path.GetDirectoryName(app),data);}catch{try{UpdateRecovery.Status(data,"restore_failed");}catch{}Log(log,"自动恢复未完成，请保留数据并重新运行原安装包。");}Restart(app);return 8;}
    }
    static void Wait(int pid){try{using(var process=Process.GetProcessById(pid)){if(!process.WaitForExit(120000))throw new IOException("Application is still running");}}catch(ArgumentException){}}
    static void Restart(string app){try{if(File.Exists(app))Process.Start(new ProcessStartInfo(app){UseShellExecute=true,WorkingDirectory=Path.GetDirectoryName(app)});}catch{}}
}
