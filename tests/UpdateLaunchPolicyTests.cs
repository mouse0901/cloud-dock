using System;
using System.IO;
using System.Security.Cryptography;

class UpdateLaunchPolicyTests {
    static void Assert(bool value,string name){if(!value)throw new Exception(name);}
    static string Hash(string path){using(var input=File.OpenRead(path))using(var sha=SHA256.Create())return BitConverter.ToString(sha.ComputeHash(input)).Replace("-","").ToLowerInvariant();}
    static int Main(){
        string root=Path.Combine(Path.GetTempPath(),"CloudDock-update-policy-"+Guid.NewGuid().ToString("N"));
        try{
            string data=Path.Combine(root,"data"),updates=Path.Combine(data,"updates");Directory.CreateDirectory(updates);
            string installer=Path.Combine(updates,"CloudDock-0.6.3-Setup-x64.exe");File.WriteAllBytes(installer,new byte[]{1,2,3,4});
            string error,digest=Hash(installer);
            Assert(UpdateLaunchPolicy.Validate(installer,data,digest,"0.6.3",out error),"valid package rejected");
            Assert(!UpdateLaunchPolicy.Validate(installer,data,new string('a',64),"0.6.3",out error),"wrong hash accepted");
            string outside=Path.Combine(root,"CloudDock-0.6.3-Setup-x64.exe");File.Copy(installer,outside);
            Assert(!UpdateLaunchPolicy.Validate(outside,data,digest,"0.6.3",out error),"outside path accepted");
            Assert(!UpdateLaunchPolicy.Validate(installer,data,digest,"../0.6.3",out error),"bad version accepted");
            Console.WriteLine("UPDATE_LAUNCH_POLICY_PASS");return 0;
        }finally{try{Directory.Delete(root,true);}catch{}}
    }
}
