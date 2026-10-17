using System;
class NativeBridgePolicyTests {
    static int count;
    static void Check(bool value,string name){count++;if(!value)throw new Exception(name);}
    static void Main(){
        string page="https://clouddock.invalid",bridge="https://bridge.clouddock.invalid/",token=new string('a',43);
        Check(NativeBridgePolicy.IsPage(page+"/index.html"),"bundled page");
        Check(!NativeBridgePolicy.IsPage(page+".example/index.html"),"lookalike page rejected");
        Check(!NativeBridgePolicy.IsPage("http://clouddock.invalid/index.html"),"page scheme");
        Check(!NativeBridgePolicy.IsPage("https://clouddock.invalid:1234/index.html"),"page port");
        Check(NativeBridgePolicy.IsBackend("http://127.0.0.1:1234/"+token+"/"),"private loopback");
        Check(!NativeBridgePolicy.IsBackend("http://example.com/"+token+"/"),"remote backend rejected");
        Check(!NativeBridgePolicy.IsBackend("http://127.0.0.1/"),"missing token");
        Check(!NativeBridgePolicy.IsBackend("http://user@127.0.0.1/"+token+"/"),"userinfo rejected");
        Check(NativeBridgePolicy.Allows(bridge+"api/state","GET",page),"state read");
        Check(NativeBridgePolicy.Allows(bridge+"api/action","POST",page),"explicit action");
        Check(NativeBridgePolicy.Allows(bridge+"api/action","OPTIONS",page),"action preflight");
        Check(!NativeBridgePolicy.Allows(bridge+"api/action","POST","https://evil.example"),"foreign action origin");
        Check(!NativeBridgePolicy.Allows(bridge+"api/action","POST",""),"missing action origin");
        Check(!NativeBridgePolicy.Allows(bridge+"api/selection","POST",page),"native picker cannot be forged");
        Check(!NativeBridgePolicy.Allows(bridge+"data/sessions/baidu.json","GET",page),"private files inaccessible");
        Check(!NativeBridgePolicy.Allows(bridge+"api/state?x=1","GET",page),"query rejected");
        Check(!NativeBridgePolicy.Allows(bridge+"api/state","DELETE",page),"unexpected method");
        Check(NativeBridgePolicy.Allows(bridge+"api/thumbnail/"+new string('a',64),"GET",""),"image request");
        Check(!NativeBridgePolicy.Allows(bridge+"api/thumbnail/../../data","GET",page),"thumbnail traversal");
        Check(!NativeBridgePolicy.Allows(bridge+"api/thumbnail/"+new string('a',64),"POST",page),"thumbnail read only");
        Console.WriteLine("PASS "+count+" native bridge policy checks");
    }
}
