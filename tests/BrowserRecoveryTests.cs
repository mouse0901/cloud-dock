using System;
class BrowserRecoveryTests {
 static void Main(){
  if(BrowserRecovery.Action("GpuProcessExited")!="ignore")throw new Exception("gpu");
  if(BrowserRecovery.Action("UtilityProcessExited")!="ignore")throw new Exception("utility");
  if(BrowserRecovery.Action("FrameRenderProcessExited")!="ignore")throw new Exception("frame");
  if(BrowserRecovery.Action("RenderProcessExited")!="reload")throw new Exception("render");
  if(BrowserRecovery.Action("BrowserProcessExited")!="recreate")throw new Exception("browser");
  var r=new BrowserRecovery();var t=DateTime.UtcNow;
  if(!r.Allow(t)||!r.Allow(t)||r.Allow(t)||!r.Allow(t.AddSeconds(61)))throw new Exception("budget");
  Console.WriteLine("BrowserRecovery: 6 checks passed");
 }
}
