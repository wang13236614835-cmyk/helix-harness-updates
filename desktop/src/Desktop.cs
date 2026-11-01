using System;
using System.IO;
using System.Text;
using System.Drawing;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Security.Cryptography;
using System.Web.Script.Serialization;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Reflection;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
[assembly: AssemblyTitle("Helix 工作台")]
[assembly: AssemblyProduct("Helix Harness")]
[assembly: AssemblyVersion("3.2.0.0")]
static class Program {
    [DllImport("shell32.dll",CharSet=CharSet.Unicode)] static extern int SetCurrentProcessExplicitAppUserModelID(string id);
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        string root=null;
        for(int i=0;i+1<args.Length;i++) if(args[i]=="--root") root=Path.GetFullPath(args[i+1]);
        if(root==null) { MessageBox.Show("请使用 Helix.exe 或桌面上的 Helix 工作台打开应用。"); return 1; }
        string identity;
        using(var sha=SHA256.Create()) identity=BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(root.ToLowerInvariant()))).Replace("-","").Substring(0,20);
        bool first;
        using(var stopSignal=new EventWaitHandle(false,EventResetMode.AutoReset,"Local\\Helix.Stop."+identity)) {
        if(Array.IndexOf(args,"--shutdown")>=0) {stopSignal.Set();return 0;}
        using(var mutex=new Mutex(true,"Local\\Helix.Desktop."+identity,out first))
        using(var signal=new EventWaitHandle(false,EventResetMode.AutoReset,"Local\\Helix.Activate."+identity)) {
            if(!first) { signal.Set(); return 0; }
            SetCurrentProcessExplicitAppUserModelID("Helix.Workbench."+identity);
            try { Application.Run(new DesktopForm(root,signal,stopSignal)); return 0; }
            catch(Exception ex) { MessageBox.Show("应用未能启动："+ex.Message,"Helix",MessageBoxButtons.OK,MessageBoxIcon.Error); return 1; }
        }
        }
    }
}
class DesktopForm:Form {
    readonly string root, data, ownEntry;
    readonly EventWaitHandle signal,stopSignal;
    readonly JavaScriptSerializer json=new JavaScriptSerializer();
    readonly WebView2 view=new WebView2();
    readonly HelixSplash loading=new HelixSplash();
    readonly System.Windows.Forms.Timer timer=new System.Windows.Forms.Timer();
    CoreWebView2Environment webEnvironment;
    string origin="", instanceId="", currentVersion="3.2.0", updateEntry="";
    bool restarting=false, closing=false, polling=false;
    int stableChecks=0;
    string sessionScript="";
    public DesktopForm(string root,EventWaitHandle signal,EventWaitHandle stopSignal) {
        this.root=root; this.signal=signal; this.stopSignal=stopSignal; ownEntry=Application.ExecutablePath;
        data=Path.Combine(root,"shell","data","desktop"); Directory.CreateDirectory(data);
        Text="Helix 工作台"; StartPosition=FormStartPosition.CenterScreen; Size=new Size(1380,900); MinimumSize=new Size(960,640);
        BackColor=Color.FromArgb(246,249,249); Font=new Font("Microsoft YaHei UI",10);
        try { Icon=Icon.ExtractAssociatedIcon(Path.Combine(root,"Helix.exe")); } catch {}
        RestoreWindow();
        loading.Text="正在打开工作台…"; loading.Dock=DockStyle.Fill;
        loading.Finished+=delegate { loading.Visible=false; view.Visible=true; view.BringToFront(); };
        view.Dock=DockStyle.Fill; view.DefaultBackgroundColor=BackColor; view.Visible=false;
        Controls.Add(view); Controls.Add(loading);
        Shown+=async delegate { await OpenWorkbench(); };
        FormClosing+=OnClosing;
        timer.Interval=2000; timer.Tick+=async delegate { await Poll(); }; timer.Start();
    }
    string StateFile(string name) { return Path.Combine(data,name); }
    Dictionary<string,object> ReadJson(string path) {
        try { return json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path)); } catch { return new Dictionary<string,object>(); }
    }
    void AtomicWrite(string path,string value) {
        string tmp=path+".tmp"; File.WriteAllText(tmp,value,Encoding.UTF8);
        if(File.Exists(path)) File.Replace(tmp,path,null); else File.Move(tmp,path);
    }
    void Log(string text) { try { File.AppendAllText(StateFile("desktop.log"),DateTime.Now.ToString("s")+" "+text+Environment.NewLine); } catch {} }
    void RestoreWindow() {
        var state=ReadJson(StateFile("window.json"));
        try {
            if(state.ContainsKey("width")) {
                var bounds=new Rectangle(Convert.ToInt32(state["x"]),Convert.ToInt32(state["y"]),Math.Max(960,Convert.ToInt32(state["width"])),Math.Max(640,Convert.ToInt32(state["height"])));
                foreach(var screen in Screen.AllScreens) if(Rectangle.Intersect(bounds,screen.WorkingArea).Width>=150) { StartPosition=FormStartPosition.Manual; Bounds=bounds; break; }
                if(state.ContainsKey("maximized")&&Convert.ToBoolean(state["maximized"])) WindowState=FormWindowState.Maximized;
            }
        } catch {}
    }
    void SaveWindow() {
        try {
            Rectangle r=WindowState==FormWindowState.Normal?Bounds:RestoreBounds;
            string route="/";
            if(view.Source!=null&&IsLocal(view.Source.AbsoluteUri)) route=view.Source.PathAndQuery+view.Source.Fragment;
            // 登录页不替代上次工作位置。
            if(route.StartsWith("/login")||route.StartsWith("/leader-login")||route.StartsWith("/register")) route="/";
            AtomicWrite(StateFile("window.json"),json.Serialize(new {x=r.X,y=r.Y,width=r.Width,height=r.Height,maximized=WindowState==FormWindowState.Maximized,route=route}));
        } catch {}
    }
    ProcessStartInfo HiddenPython(string arguments) {
        var si=new ProcessStartInfo(Path.Combine(root,"runtime","python","python.exe"),"\""+Path.Combine(root,"portable_launcher.py")+"\" "+arguments);
        si.WorkingDirectory=root; si.UseShellExecute=false; si.CreateNoWindow=true; si.WindowStyle=ProcessWindowStyle.Hidden;
        si.RedirectStandardOutput=true; si.RedirectStandardError=true; si.StandardOutputEncoding=Encoding.UTF8; si.StandardErrorEncoding=Encoding.UTF8;
        si.EnvironmentVariables["PYTHONUTF8"]="1";
        return si;
    }
    async Task<string> StartBackend() {
        using(var p=Process.Start(HiddenPython("--no-browser"))) {
            var output=p.StandardOutput.ReadToEndAsync(); var errors=p.StandardError.ReadToEndAsync();
            bool exited=await Task.Run(()=>p.WaitForExit(75000));
            if(!exited) { try { p.Kill(); } catch {} throw new Exception("启动超时。请重试；项目和账号仍保存在原目录。"); }
            string raw=await output; string err=await errors;
            if(p.ExitCode!=0) { Log("backend startup failed "+err); throw new Exception("工作台服务未能启动，请在安装目录 shell/data 查看运行日志。"); }
            var reply=json.Deserialize<Dictionary<string,object>>(raw.Trim());
            instanceId=Convert.ToString(reply["instance_id"]);
            var uri=new Uri(Convert.ToString(reply["url"]));
            if(uri.Scheme!="http"||uri.Host!="127.0.0.1") throw new Exception("工作台地址无效。");
            return uri.GetLeftPart(UriPartial.Authority);
        }
    }
    bool IsLocal(string url) {
        Uri u;
        return Uri.TryCreate(url,UriKind.Absolute,out u)&&u.GetLeftPart(UriPartial.Authority)==origin;
    }
    string ReadSession() {
        try { return Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(StateFile("session.dpapi")),Encoding.UTF8.GetBytes(root.ToLowerInvariant()),DataProtectionScope.CurrentUser)); } catch { return ""; }
    }
    void RememberSession(string token) {
        if(token.Length>8192) return;
        if(token.Length==0) { try { File.Delete(StateFile("session.dpapi")); } catch {} return; }
        byte[] raw=ProtectedData.Protect(Encoding.UTF8.GetBytes(token),Encoding.UTF8.GetBytes(root.ToLowerInvariant()),DataProtectionScope.CurrentUser);
        File.WriteAllBytes(StateFile("session.dpapi"),raw);
    }
    string SessionBridge() {
        // 只在本安装的本机来源恢复会话；Windows 当前用户加密，不保存明文密码。
        return "(()=>{if(location.origin!=="+json.Serialize(origin)+")return;const t="+json.Serialize(ReadSession())+";if(t&&!sessionStorage.getItem('helix.token'))sessionStorage.setItem('helix.token',t);const set=Storage.prototype.setItem,del=Storage.prototype.removeItem,clear=Storage.prototype.clear;Storage.prototype.setItem=function(k,v){set.call(this,k,v);if(this===sessionStorage&&k==='helix.token')chrome.webview.postMessage({type:'helix-session',token:String(v)});};Storage.prototype.removeItem=function(k){del.call(this,k);if(this===sessionStorage&&k==='helix.token')chrome.webview.postMessage({type:'helix-session',token:''});};Storage.prototype.clear=function(){clear.call(this);if(this===sessionStorage)chrome.webview.postMessage({type:'helix-session',token:''});};})();";
    }
    async Task OpenWorkbench() {
        try {
            loading.Text="正在连接本机项目…";
            origin=await StartBackend();
            if(closing) return;
            loading.Text="正在准备工作台界面…";
            webEnvironment=await CoreWebView2Environment.CreateAsync(null,StateFile("webview2"));
            await view.EnsureCoreWebView2Async(webEnvironment);
            view.CoreWebView2.Settings.AreDevToolsEnabled=false;
            view.CoreWebView2.Settings.AreHostObjectsAllowed=false;
            view.CoreWebView2.Settings.IsStatusBarEnabled=false;
            view.CoreWebView2.Settings.IsPasswordAutosaveEnabled=false;
            view.CoreWebView2.Settings.IsGeneralAutofillEnabled=false;
            view.CoreWebView2.Settings.AreBrowserAcceleratorKeysEnabled=true;
            string remembered=ReadSession();
            if(remembered.Length>0) {
                var cookie=view.CoreWebView2.CookieManager.CreateCookie("helix_sid",remembered,"127.0.0.1","/");
                cookie.IsHttpOnly=true; cookie.SameSite=CoreWebView2CookieSameSiteKind.Strict;
                view.CoreWebView2.CookieManager.AddOrUpdateCookie(cookie);
            }
            sessionScript=await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(SessionBridge());
            view.CoreWebView2.WebMessageReceived+=OnMessage;
            view.CoreWebView2.NavigationStarting+=(s,e)=> { if(!IsLocal(e.Uri)&&e.Uri!="about:blank") { e.Cancel=true; OpenExternal(e.Uri); } };
            view.CoreWebView2.NewWindowRequested+=(s,e)=> { e.Handled=true; if(IsLocal(e.Uri)) OpenDocument(e.Uri); else OpenExternal(e.Uri); };
            view.CoreWebView2.ProcessFailed+=(s,e)=> { Log("webview process failed "+e.ProcessFailedKind); loading.Text="页面暂时停止响应，请关闭后重新打开 Helix。"; loading.Visible=true; loading.BringToFront(); };
            view.CoreWebView2.NavigationCompleted+=(s,e)=> { if(e.IsSuccess) { loading.Text="工作台已就绪"; loading.Complete(); } else { loading.Text="工作台正在重新连接…"; loading.Visible=true; loading.BringToFront(); } };
            view.CoreWebView2.SourceChanged+=(s,e)=>SaveWindow();
            var saved=ReadJson(StateFile("window.json")); string route=saved.ContainsKey("route")?Convert.ToString(saved["route"]):"/";
            if(!route.StartsWith("/")||route.StartsWith("//")) route="/";
            view.CoreWebView2.Navigate(origin+route);
            Log("started "+ownEntry);
        } catch(Exception ex) {
            Log("startup "+ex.GetType().Name+" "+ex.Message);
            loading.Text="Helix 启动失败\r\n"+ex.Message+"\r\n关闭后可重新打开。";
        }
    }
    async void OnMessage(object sender,CoreWebView2WebMessageReceivedEventArgs e) {
        if(!IsLocal(e.Source)) return;
        try {
            var m=json.Deserialize<Dictionary<string,object>>(e.WebMessageAsJson);
            if(Convert.ToString(m["type"])!="helix-session") return;
            RememberSession(Convert.ToString(m["token"]));
            view.CoreWebView2.RemoveScriptToExecuteOnDocumentCreated(sessionScript);
            sessionScript=await view.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(SessionBridge());
        } catch(Exception ex) { Log("session persistence "+ex.GetType().Name); }
    }
    void OpenExternal(string url) {
        Uri u; if(!Uri.TryCreate(url,UriKind.Absolute,out u)||!(u.Scheme=="https"||u.Scheme=="http"||u.Scheme=="mailto")) return;
        try { Process.Start(new ProcessStartInfo(url){UseShellExecute=true}); } catch { MessageBox.Show(this,"无法打开此链接。","Helix"); }
    }
    async void OpenDocument(string url) {
        var child=new Form {Text="Helix · 文件预览",Size=new Size(1100,800),StartPosition=FormStartPosition.CenterParent,Icon=Icon};
        var doc=new WebView2 {Dock=DockStyle.Fill}; child.Controls.Add(doc); child.Show(this);
        try {
            await doc.EnsureCoreWebView2Async(webEnvironment);
            doc.CoreWebView2.Settings.AreDevToolsEnabled=false; doc.CoreWebView2.Settings.AreHostObjectsAllowed=false;
            await doc.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(SessionBridge());
            doc.CoreWebView2.NavigationStarting+=(s,e)=> { if(!IsLocal(e.Uri)&&e.Uri!="about:blank") {e.Cancel=true;OpenExternal(e.Uri);} };
            doc.CoreWebView2.NewWindowRequested+=(s,e)=> {e.Handled=true;OpenExternal(e.Uri);};
            doc.Source=new Uri(url);
            child.FormClosed+=delegate {doc.Dispose();};
        } catch { child.Close(); MessageBox.Show(this,"文件无法预览，请尝试下载。","Helix"); }
    }
    async Task<bool> BackendHealthy() {
        try {
            using(var client=new System.Net.WebClient()) {
                client.Proxy=null;
                string text=await client.DownloadStringTaskAsync(origin+"/api/health");
                var h=json.Deserialize<Dictionary<string,object>>(text);
                return h.ContainsKey("release")&&Convert.ToString(h["release"])==currentVersion&&h.ContainsKey("instance_id")&&Convert.ToString(h["instance_id"])==instanceId;
            }
        } catch {return false;}
    }
    async Task Poll() {
        if(stopSignal.WaitOne(0)) {Close();return;}
        if(signal.WaitOne(0)) { if(WindowState==FormWindowState.Minimized) WindowState=FormWindowState.Normal; Show(); Activate(); }
        if(polling||closing||origin.Length==0||view.CoreWebView2==null) return;
        polling=true;
        try {
            var pointer=ReadJson(Path.Combine(root,"desktop","current.json"));
            if(!pointer.ContainsKey("entry")) return;
            string entry=Path.GetFullPath(Path.Combine(root,Convert.ToString(pointer["entry"])));
            if(String.Equals(entry,ownEntry,StringComparison.OrdinalIgnoreCase)) { stableChecks=0; return; }
            if(!entry.StartsWith(Path.Combine(root,"desktop","versions")+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)||!File.Exists(entry)) return;
            currentVersion=Convert.ToString(pointer["version"]);
            var state=ReadJson(Path.Combine(root,"shell","data","update","state.json"));
            if(state.ContainsKey("pending_verify")&&state["pending_verify"]!=null) { stableChecks=0; return; }
            if(!await BackendHealthy()) {stableChecks=0;return;}
            if(updateEntry!=entry) { updateEntry=entry; stableChecks=0; }
            if(++stableChecks<2) return;
            SaveWindow();
            // 将短命引导程序与实际窗口分开，更新不会覆盖正在运行的 EXE/DLL。
            var si=new ProcessStartInfo(Path.Combine(root,"Helix.exe"),"--wait-for-pid "+Process.GetCurrentProcess().Id) {WorkingDirectory=root,UseShellExecute=false,CreateNoWindow=true};
            Process.Start(si); restarting=true; Close();
        } catch(Exception ex) {Log("update handover "+ex.GetType().Name);}
        finally {polling=false;}
    }
    void OnClosing(object sender,FormClosingEventArgs e) {
        closing=true; timer.Stop(); SaveWindow();
        if(view.CoreWebView2!=null) view.Dispose();
        // 后台继续保存项目和完成在途任务；退出不会中断已开始的计算或同步。
        Log(restarting?"handover to updated desktop":"window closed; background jobs retained");
    }
}
