using System;
using System.Diagnostics;
using System.IO;
using System.Text;

class UpdateLauncher {
    static string Quote(string value){return "\""+(value??"").Replace("\"","\\\"")+"\"";}
    static void Log(string path,string message){
        try{File.AppendAllText(path,DateTime.UtcNow.ToString("o")+" "+message+Environment.NewLine,new UTF8Encoding(false));}catch{}
    }
    static int Main(string[] args){
        if(args.Length!=7)return 2;
        string installer=args[0],sha=args[1],version=args[2],app=args[3],data=args[4],log=args[5];int parent;
        if(!Int32.TryParse(args[6],out parent))return 2;
        string error;
        if(!UpdateLaunchPolicy.Validate(installer,data,sha,version,out error)){Log(log,error);return 3;}
        try{
            try{using(var process=Process.GetProcessById(parent)){if(!process.WaitForExit(120000)){Log(log,"主程序未能安全退出，安装已取消。");return 4;}}}catch(ArgumentException){}
            var setup=new ProcessStartInfo(installer,"/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE=1 /LOG="+Quote(log));
            setup.UseShellExecute=false;setup.WorkingDirectory=Path.GetDirectoryName(installer);
            using(var process=Process.Start(setup)){
                if(!process.WaitForExit(300000)){Log(log,"安装程序运行超时。");return 5;}
                if(process.ExitCode!=0){Log(log,"安装未完成，退出码 "+process.ExitCode);Restart(app);return 6;}
            }
            if(!File.Exists(app)){Log(log,"安装完成但主程序不存在。");return 7;}
            Log(log,"更新至 "+version+" 完成。");Restart(app);return 0;
        }catch(Exception error2){Log(log,"更新启动失败："+error2.GetType().Name);Restart(app);return 8;}
    }
    static void Restart(string app){try{if(File.Exists(app))Process.Start(new ProcessStartInfo(app){UseShellExecute=true,WorkingDirectory=Path.GetDirectoryName(app)});}catch{}}
}
