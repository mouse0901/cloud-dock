using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

partial class CloudWindow {
    async Task<bool> LongevityMessage(string type,Dictionary<string,object> data){
        if(type=="longevityStatus"){
            Send(new {type="longevityStatus",recovery=UpdateRecovery.Public(Program.Data),host_memory=Process.GetCurrentProcess().WorkingSet64});return true;
        }
        if(type=="recoverUpdate"){
            try{
            await Post("api/action",new {action="recovery_prepare"});
            var state=UpdateRecovery.Read(Program.Data);if(state==null)throw new IOException("No recovery snapshot");
            string updates=Path.Combine(Program.Data,"updates"),helper=Path.Combine(updates,"RecoveryLauncher-"+Process.GetCurrentProcess().Id+".exe");
            File.Copy(Path.Combine(root,"UpdateLauncher.exe"),helper,true);
            string args="--recover "+Q(Path.Combine(root,"CloudDock.exe"))+" "+Q(Program.Data)+" "+Process.GetCurrentProcess().Id;
            Process.Start(new ProcessStartInfo(helper,args){UseShellExecute=false,WorkingDirectory=updates,CreateNoWindow=true});closeToTray=false;Close();return true;
            }catch{Send(new {type="recoveryError",message="恢复尚未开始。请先暂停任务，并确认恢复点和安装目录可用。"});return true;}
        }
        if(type!="exportSettings"&&type!="importSettings")return false;
        if(picking)return true;picking=true;
        try{
            if(type=="exportSettings"){
                using(var save=new SaveFileDialog()){save.Title="导出云屿设置（不含账号与文件位置）";save.Filter="云屿设置 (*.json)|*.json";save.FileName="CloudDock-settings.json";
                    if(save.ShowDialog(this)==DialogResult.OK){var value=await Post("api/action",new {action="settings_export"});value.Remove("ok");File.WriteAllText(save.FileName,json.Serialize(value),new UTF8Encoding(false));Send(new {type="settingsExported"});}}
            }else{
                using(var open=new OpenFileDialog()){open.Title="选择云屿设置文件";open.Filter="云屿设置 (*.json)|*.json";
                    if(open.ShowDialog(this)==DialogResult.OK){if(new FileInfo(open.FileName).Length>65536)throw new IOException("Settings file too large");var value=await Post("api/action",new {action="settings_preview",text=File.ReadAllText(open.FileName,Encoding.UTF8)});Send(new {type="settingsPreview",preview=value});}}
            }
        }catch{Send(new {type="settingsFileError",message="设置文件未处理，请检查格式、文件大小和目录权限。"});}
        finally{picking=false;}
        return true;
    }
}
