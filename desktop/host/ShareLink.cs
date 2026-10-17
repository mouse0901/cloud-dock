using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;

// Pure parsing: no clipboard access, network, logging, or persistence.
sealed class ShareLink {
    public string provider, url, password;
    public string Key { get { return provider+":"+url; } }
    static readonly Regex Link=new Regex(@"(?<![\w@/=%.-])https?://[^\s<>""'\)\]\}，。；]+",RegexOptions.IgnoreCase);
    public static ShareLink Parse(string text){
        if(String.IsNullOrEmpty(text)||text.Length>16384)return null;
        text=text.Replace(@"\:",":").Replace(@"\&","&");
        foreach(Match match in Link.Matches(text)){
            Uri uri;if(!Uri.TryCreate(match.Value,UriKind.Absolute,out uri)||!uri.IsDefaultPort||uri.UserInfo!="")continue;
            string host=uri.Host.ToLowerInvariant();
            if(host!="pan.baidu.com"&&host!="pan.quark.cn")continue;
            if(!Regex.IsMatch(uri.AbsolutePath,@"^/s/[A-Za-z0-9_-]{6,128}$"))continue;
            string code="";
            foreach(string pair in uri.Query.TrimStart('?').Split('&')){
                var parts=pair.Split(new char[]{'='},2);
                if(parts.Length==2&&parts[0].Equals("pwd",StringComparison.OrdinalIgnoreCase)){
                    string value=Uri.UnescapeDataString(parts[1]);if(Regex.IsMatch(value,@"^[A-Za-z0-9]{4,8}$"))code=value;break;
                }
            }
            if(code==""){
                int start=match.Index+match.Length,length=Math.Min(120,text.Length-start);
                string tail=text.Substring(start,length);int next=tail.IndexOf("http",StringComparison.OrdinalIgnoreCase);if(next>=0)tail=tail.Substring(0,next);
                var pass=Regex.Match(tail,@"(?:提取码|提取密码|密码)\s*[:：]?\s*([A-Za-z0-9]{4,8})(?![A-Za-z0-9])");if(pass.Success)code=pass.Groups[1].Value;
            }
            return new ShareLink{provider=host=="pan.baidu.com"?"baidu":"quark",password=code,url="https://"+host+uri.AbsolutePath+(code==""?"":"?pwd="+code)};
        }
        return null;
    }
}

sealed class ShareLinkHistory {
    readonly HashSet<string> seen=new HashSet<string>();
    readonly Queue<string> order=new Queue<string>();
    public bool Accept(ShareLink link){
        if(link==null||!seen.Add(link.Key))return false;
        order.Enqueue(link.Key);while(order.Count>64)seen.Remove(order.Dequeue());return true;
    }
}
