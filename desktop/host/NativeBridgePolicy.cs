using System;
using System.Text.RegularExpressions;

static class NativeBridgePolicy {
    public const string PageOrigin="https://clouddock.invalid/";
    public const string BridgeOrigin="https://bridge.clouddock.invalid/";
    public static bool IsPage(string source){Uri uri;return Uri.TryCreate(source,UriKind.Absolute,out uri)&&uri.Scheme=="https"&&uri.Host=="clouddock.invalid"&&uri.IsDefaultPort&&uri.UserInfo=="";}
    public static bool IsBackend(string value){Uri uri;return Uri.TryCreate(value,UriKind.Absolute,out uri)&&uri.Scheme=="http"&&uri.Host=="127.0.0.1"&&uri.UserInfo==""&&uri.Query==""&&uri.Fragment==""&&uri.AbsolutePath.Length>=30&&value.EndsWith("/");}
    public static bool IsThumbnail(string path){return Regex.IsMatch(path,@"^api/thumbnail/[a-f0-9]{64}$");}
    public static bool IsMedia(string path){return Regex.IsMatch(path,@"^api/media/[a-f0-9]{64}$");}
    public static bool Allows(string address,string method,string origin){
        Uri uri;if(!Uri.TryCreate(address,UriKind.Absolute,out uri)||uri.Scheme!="https"||uri.Host!="bridge.clouddock.invalid"||!uri.IsDefaultPort||uri.UserInfo!=""||uri.Query!=""||uri.Fragment!="")return false;
        string path=uri.AbsolutePath.TrimStart('/');
        if(IsThumbnail(path)||IsMedia(path))return method=="GET" && (origin==""||origin==PageOrigin.TrimEnd('/'));
        if(origin!=PageOrigin.TrimEnd('/'))return false;
        return method=="GET"&&path=="api/state" || (method=="POST"||method=="OPTIONS")&&path=="api/action";
    }
}
