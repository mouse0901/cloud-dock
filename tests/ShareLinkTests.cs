using System;
class ShareLinkTests {
 static int count;
 static void Check(bool value,string name){count++;if(!value)throw new Exception(name);}
 static void Main(){
  var baidu=ShareLink.Parse("https://pan.baidu.com/s/1LqkXcCyItQ51jM-cj8IdFw?pwd=gvcr#list/path=%2F&vmode=list");
  Check(baidu!=null&&baidu.provider=="baidu"&&baidu.password=="gvcr","Baidu supplied example");
  Check(baidu.url=="https://pan.baidu.com/s/1LqkXcCyItQ51jM-cj8IdFw?pwd=gvcr","fragment removed code retained");
  var quark=ShareLink.Parse(@"夸克的：https\://pan.quark.cn/s/11bbafa97c9b?pwd=W2pz");
  Check(quark!=null&&quark.provider=="quark"&&quark.password=="W2pz","escaped Quark supplied example");
  Check(ShareLink.Parse("[链接](https://pan.quark.cn/s/11bbafa97c9b?pwd=W2pz)").url==quark.url,"markdown");
  Check(ShareLink.Parse("分享 https://pan.quark.cn/s/11bbafa97c9b\n提取码：Ab12").password=="Ab12","copied sentence");
  Check(ShareLink.Parse("https://pan.baidu.com/s/1LqkXcCyItQ51jM-cj8IdFw").password=="","no code");
  Check(ShareLink.Parse("http://pan.quark.cn/s/11bbafa97c9b").url.StartsWith("https://"),"upgrade HTTP");
  Check(ShareLink.Parse("https://pan.quark.cn/s/11bbafa97c9b?pwd=%57%32%70%7A").password=="W2pz","encoded code");
  foreach(string text in new[]{"ordinary text","https://pan.quark.cn.evil.example/s/11bbafa97c9b","https://pan.quark.cn@evil.example/s/11bbafa97c9b","https://pan.quark.cn:8443/s/11bbafa97c9b","https://pan.quark.cn/s/11bbafa97c9b/extra","https://pan.quark.cn/s/11bbafa97c9b.exe","javascript:alert(1)",new string('a',16385)})Check(ShareLink.Parse(text)==null,"reject unsafe/non-share text");
  Check(ShareLink.Parse("https://pan.quark.cn/s/11bbafa97c9b?pwd=bad%22code").password=="","reject unsafe code");
  Check(ShareLink.Parse("https://pan.quark.cn/s/11bbafa97c9b https://pan.quark.cn/s/22bbafa97c9b 提取码：Ab12").password=="","do not use another link code");
  var many=ShareLink.ParseAll("https://pan.baidu.com/share/init?surl=abcdef&pwd=Ab12 https://pan.quark.cn/s/abcdef12 提取码：Cd34");
  Check(many.Count==2&&many[0].url=="https://pan.baidu.com/s/1abcdef?pwd=Ab12"&&many[1].password=="Cd34","two providers and legacy link");
  Check(ShareLink.ParseAll("https://pan.quark.cn/s/abcdef12 https://pan.quark.cn/s/abcdef12").Count==1,"same clipboard duplicate");
  var history=new ShareLinkHistory();Check(history.Accept(quark),"first offer");Check(!history.Accept(ShareLink.Parse(quark.url+"#folder")),"repeat not offered");Check(!history.Accept(null),"ordinary clipboard not retained");Check(history.Accept(ShareLink.Parse("https://pan.quark.cn/s/11bbafa97c9b?pwd=Ab12")),"changed code accepted");
  Console.WriteLine("PASS "+count+" share recognition checks");
 }
}
