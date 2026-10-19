using System;
using System.IO;
using System.Text;
using System.Collections.Generic;
using System.Web.Script.Serialization;
class RecoveryTests {
 static int count;static void Check(bool value,string name){if(!value)throw new Exception(name);count++;}
 static void Throws(Action action,string name){try{action();}catch{count++;return;}throw new Exception(name);}
 static void Main(string[] args){
  string test=args[0],root=Path.Combine(test,"app"),data=Path.Combine(test,"profile");Directory.CreateDirectory(root);Directory.CreateDirectory(data);
  File.WriteAllText(Path.Combine(root,"CloudDock.exe"),"old-program");File.WriteAllText(Path.Combine(root,"version.json"),"{\"version\":\"0.6.11\"}");File.WriteAllText(Path.Combine(root,"installed-data.txt"),data);File.WriteAllText(Path.Combine(data,"private.txt"),"user-data");
  var lines=new List<string>();foreach(string file in new[]{"CloudDock.exe","version.json"})lines.Add(UpdateRecovery.Hash(Path.Combine(root,file))+"  "+file);File.WriteAllLines(Path.Combine(root,"CHECKSUMS.sha256"),lines);
  Throws(()=>UpdateRecovery.Safe(root,"../escape"),"dot traversal");Throws(()=>UpdateRecovery.Safe(root,"C:\\escape"),"absolute");Throws(()=>UpdateRecovery.Safe(root,"folder/file:stream"),"stream");Throws(()=>UpdateRecovery.Safe(root,"folder//file"),"empty component");
  Throws(()=>UpdateRecovery.Prepare(root,Path.Combine(root,"data"),"0.6.12"),"data nesting");
  string id=UpdateRecovery.Prepare(root,data,"0.6.12");Check(UpdateRecovery.Read(data)["status"].Equals("prepared"),"prepared");
  string backup=Path.Combine(UpdateRecovery.DirectoryFor(data),id);Check(!File.Exists(Path.Combine(backup,"installed-data.txt")),"configuration excluded");Check(!File.Exists(Path.Combine(backup,"private.txt")),"data excluded");
  File.WriteAllText(Path.Combine(root,"CloudDock.exe"),"broken-new-program");File.WriteAllText(Path.Combine(root,"version.json"),"{\"version\":\"0.6.12\"}");UpdateRecovery.Status(data,"installed");UpdateRecovery.Healthy(data,root);Check(UpdateRecovery.Read(data)["status"].Equals("healthy"),"health acknowledgement");
  Throws(()=>UpdateRecovery.Restore(Path.Combine(test,"other"),data),"foreign root");UpdateRecovery.Restore(root,data);Check(File.ReadAllText(Path.Combine(root,"CloudDock.exe"))=="old-program","program restored");Check(File.ReadAllText(Path.Combine(data,"private.txt"))=="user-data","data preserved");Check(File.ReadAllText(Path.Combine(root,"installed-data.txt"))==data,"data pointer preserved");Check(UpdateRecovery.Read(data)["status"].Equals("restored"),"restored status");
  File.WriteAllText(Path.Combine(backup,"CloudDock.exe"),"tampered");File.WriteAllText(Path.Combine(root,"CloudDock.exe"),"current-program");Throws(()=>UpdateRecovery.Restore(root,data),"changed backup rejected");Check(File.ReadAllText(Path.Combine(root,"CloudDock.exe"))=="current-program","all verified before writes");
  Throws(()=>UpdateRecovery.Prepare(root,data,"0.6.13"),"modified installation rejected");
  File.WriteAllText(Path.Combine(root,"CloudDock.exe"),"old-program");
  File.WriteAllText(Path.Combine(backup,"CloudDock.exe"),"old-program");
  string next=UpdateRecovery.Prepare(root,data,"0.6.13");Check(next!=id,"new recovery generation");Check(!Directory.Exists(backup),"verified previous backup retired");Check(Directory.Exists(Path.Combine(UpdateRecovery.DirectoryFor(data),next)),"latest snapshot retained");
  File.AppendAllText(Path.Combine(root,"CHECKSUMS.sha256"),new string('a',64)+"  ../private.txt\n");Throws(()=>UpdateRecovery.Files(root),"manifest traversal");
  Console.WriteLine("Recovery checks passed: "+count);
 }
}
