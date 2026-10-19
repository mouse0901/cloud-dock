using System;
using System.IO;
class RecoveryFakeApp {
 static void Main(){string root=AppDomain.CurrentDomain.BaseDirectory;if(!new DirectoryInfo(root).Parent.Name.StartsWith("recovery-flow-"))return;File.WriteAllText(Path.Combine(root,"restart-observed.txt"),"started");}
}
