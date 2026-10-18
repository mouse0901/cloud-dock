using System;
using System.Collections.Generic;

// Utility/GPU subprocesses are recovered by WebView2 itself. Only main-page
// failures replace the page; repeated failures stop instead of looping forever.
class BrowserRecovery {
    readonly Queue<DateTime> attempts=new Queue<DateTime>();
    public static string Action(string kind){
        if(kind=="BrowserProcessExited")return "recreate";
        if(kind=="RenderProcessExited" || kind=="RenderProcessUnresponsive")return "reload";
        return "ignore";
    }
    public bool Allow(DateTime now){
        while(attempts.Count>0 && (now-attempts.Peek()).TotalSeconds>60)attempts.Dequeue();
        if(attempts.Count>=2)return false;
        attempts.Enqueue(now);return true;
    }
}
