"""Build the Windows GUI executables using the official Microsoft WebView2 SDK.
Usage: python desktop/src/build.py --sdk <extracted-NuGet-directory>
Requires .NET Framework csc.exe on Windows; no account or signing secret is needed.
"""
from pathlib import Path
import argparse,subprocess,shutil,json,datetime
ap=argparse.ArgumentParser();ap.add_argument('--sdk',type=Path,required=True);args=ap.parse_args()
root=Path(__file__).resolve().parents[2];src=root/'desktop/src';v=json.loads((root/'VERSION.json').read_text(encoding='utf-8-sig'))
build=datetime.datetime.now().strftime('%Y%m%d%H%M%S');version=v['version'];dest=root/'desktop/versions'/f'{version}-{build}';dest.mkdir(parents=True)
sdk=args.sdk.resolve();lib=sdk/'lib/net462'
for name in ['Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll']:shutil.copy2(lib/name,dest/name)
shutil.copy2(sdk/'runtimes/win-x64/native/WebView2Loader.dll',dest/'WebView2Loader.dll')
common=[r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe','/nologo','/target:winexe','/platform:x64','/optimize+','/win32icon:'+str(root/'shell/app/icon.ico'),'/win32manifest:'+str(src/'app.manifest'),*[f'/reference:{x}.dll' for x in ['System.Windows.Forms','System.Drawing','System.Web.Extensions','System.Security']]]
subprocess.run([*common,'/out:'+str(root/'Helix.exe'),str(src/'Bootstrap.cs')],check=True)
subprocess.run([*common,*['/reference:'+str(dest/n) for n in ['Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll']],'/out:'+str(dest/'Helix.Desktop.exe'),str(src/'Desktop.cs'),str(src/'HelixSplash.cs')],check=True)
(root/'desktop/current.json').write_text(json.dumps({'version':version,'build':build,'entry':dest.relative_to(root).as_posix()+'/Helix.Desktop.exe'},indent=2),encoding='utf-8')
print('Helix.exe and native application built successfully.')
