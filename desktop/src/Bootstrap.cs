using System;
using System.IO;
using System.Diagnostics;
using System.Threading;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using System.Collections.Generic;
using System.Reflection;
[assembly: AssemblyTitle("Helix 工作台")]
[assembly: AssemblyProduct("Helix Harness")]
[assembly: AssemblyVersion("3.2.0.0")]
static class Bootstrap {
    static void Shortcuts(string root) {
        Type type=Type.GetTypeFromProgID("WScript.Shell"); object shell=Activator.CreateInstance(type);
        try {
            string menu=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs),"Helix 工作台");Directory.CreateDirectory(menu);
            foreach(string folder in new[]{Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),menu}) {
                object link=type.InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[]{Path.Combine(folder,"Helix 工作台.lnk")});
                try {
                    Type lt=link.GetType();
                    lt.InvokeMember("TargetPath",BindingFlags.SetProperty,null,link,new object[]{Path.Combine(root,"Helix.exe")});
                    lt.InvokeMember("WorkingDirectory",BindingFlags.SetProperty,null,link,new object[]{root});
                    lt.InvokeMember("IconLocation",BindingFlags.SetProperty,null,link,new object[]{Path.Combine(root,"Helix.exe")+",0"});
                    lt.InvokeMember("Description",BindingFlags.SetProperty,null,link,new object[]{"Helix 工作台"});
                    lt.InvokeMember("Save",BindingFlags.InvokeMethod,null,link,null);
                } finally { System.Runtime.InteropServices.Marshal.ReleaseComObject(link); }
            }
        } finally {System.Runtime.InteropServices.Marshal.ReleaseComObject(shell);}
    }
    [STAThread] static int Main(string[] args) {
        Application.EnableVisualStyles();
        try {
            string root=AppDomain.CurrentDomain.BaseDirectory;
            if(Array.IndexOf(args,"--register-shortcuts")>=0) {Shortcuts(root);return 0;}
            for(int i=0;i+1<args.Length;i++) if(args[i]=="--wait-for-pid") {
                int pid; if(Int32.TryParse(args[i+1],out pid)) try {
                    using(Process p=Process.GetProcessById(pid)) {
                        string exe=p.MainModule.FileName;
                        if(exe.StartsWith(Path.Combine(root,"desktop","versions")+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)) p.WaitForExit(20000);
                    }
                } catch(ArgumentException) {} catch(InvalidOperationException) {}
            }
            var j=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(root,"desktop","current.json")));
            string entry=Path.GetFullPath(Path.Combine(root,Convert.ToString(j["entry"])));
            if(!entry.StartsWith(Path.Combine(root,"desktop","versions")+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)||Path.GetFileName(entry)!="Helix.Desktop.exe"||!File.Exists(entry)) throw new Exception("桌面程序文件不完整，请重新运行 Helix 安装程序修复。");
            string extra=Array.IndexOf(args,"--shutdown")>=0?" --shutdown":"";
            var si=new ProcessStartInfo(entry,"--root \""+root.TrimEnd('\\')+"\""+extra);
            si.WorkingDirectory=root; si.UseShellExecute=false; si.CreateNoWindow=true;
            Process.Start(si); return 0;
        } catch(Exception ex) { MessageBox.Show(ex.Message,"Helix 启动失败",MessageBoxButtons.OK,MessageBoxIcon.Error); return 1; }
    }
}
