using System;
using System.IO;
class ReleaseHelper {
    static int Main(string[] args){
        try{
            if(args.Length!=3)return 2;
            string data=Path.GetFullPath(args[1]),root=Path.GetFullPath(args[2]);
            if(args[0]=="check")return RuntimePaths.Busy(data,root)?3:0;
            if(args[0]=="exit")return RuntimePaths.RequestExit(data,root);
            return 2;
        }catch{return 4;}
    }
}
