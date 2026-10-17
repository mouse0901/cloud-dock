using System;
using System.IO;
using Microsoft.Win32;
class StartupRegistrationTests {
 static void Check(bool value,string label){if(!value)throw new Exception(label);}
 static void Main(string[] args){
  string path=Path.GetFullPath(args[0]),name="CloudDock-QA-"+Guid.NewGuid().ToString("N");
  Check(StartupRegistration.Command(path)=="\""+path+"\" --startup","quoted command");
  foreach(string invalid in new[]{"relative.exe","C:\\bad\".exe","C:\\bad\n.exe"}){
   bool rejected=false;try{StartupRegistration.Command(invalid);}catch(ArgumentException){rejected=true;}Check(rejected,"reject invalid path");
  }
  var registration=new StartupRegistration(path,name);
  try{Check(!registration.Registered(),"initially absent");registration.Set(true);Check(registration.Registered(),"enabled");registration.Set(false);Check(!registration.Registered(),"disabled");}
  finally{using(var key=Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run",true)){if(key!=null)key.DeleteValue(name,false);}}
  Console.WriteLine("Startup registration checks passed; temporary QA value removed.");
 }
}
