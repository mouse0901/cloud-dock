using System;
using System.IO;
class RecoveryPackageTests {
 static void Main(string[] args){
  string app=args[0],data=args[1];var files=UpdateRecovery.Files(app);
  foreach(var file in files)if(UpdateRecovery.Hash(UpdateRecovery.Safe(app,file.Key))!=file.Value)throw new Exception("Package file changed: "+file.Key);
  string id=UpdateRecovery.Prepare(app,data,"0.6.13");
  if(!File.Exists(Path.Combine(UpdateRecovery.DirectoryFor(data),id,"CHECKSUMS.sha256")))throw new Exception("Recovery manifest missing");
  Console.WriteLine("Clean package recovery snapshot verified: "+files.Count+" files");
 }
}
