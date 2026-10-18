"""Native desktop update payload, transaction activation and legacy transport bridge."""
import io,json,unittest,zipfile
from unittest.mock import patch
from pathlib import Path
import updater as U
import github_updates as GH
from test_updater import Updater
from test_github_updates import FakeGitHub

class DesktopUpdates(Updater):
    def native(self,root,tag='3.1.1-old'):
        d=root/'desktop/versions'/tag; d.mkdir(parents=True)
        for name in ('Helix.Desktop.exe','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll'):
            (d/name).write_bytes(b'MZ'+name.encode())
        (root/'Helix.exe').write_bytes(b'MZbootstrap')
        (root/'desktop/current.json').write_text(json.dumps({'version':'3.1.1','build':'old','entry':f'desktop/versions/{tag}/Helix.Desktop.exe'}))
        return d

    def test_native_relocated_no_locked_exe_overwrite_and_rollback(self):
        self.native(self.lead); old=self.native(self.member)
        self.use(self.lead); rel=U.make_release('native','3.2.0')
        files=U.check_package(rel['manifest'],rel['raw'])
        self.assertIn('Helix.exe',files)
        prefix=f"desktop/versions/3.2.0-{rel['manifest']['build']}/"
        self.assertIn(prefix+'Helix.Desktop.exe',files)
        self.assertFalse(any(n.startswith('desktop/versions/3.1.1-old') for n in files))
        self.use(self.member); before=(self.member/'desktop/current.json').read_bytes()
        U.stage(rel['manifest'],rel['raw']); operations=[]; replace=U.os.replace
        def record(src,dst):
            if str(src).endswith('.helix-new'):operations.append(Path(dst).relative_to(self.member).as_posix())
            return replace(src,dst)
        with patch.object(U.os,'replace',side_effect=record): self.assertTrue(U.apply_staged(self.member)['ok'])
        self.assertEqual(operations[-1],'desktop/current.json')
        self.assertTrue((old/'Helix.Desktop.exe').is_file())
        self.assertEqual(U.confirm_or_rollback(self.member,False),'rolled_back')
        self.assertEqual((self.member/'desktop/current.json').read_bytes(),before)
        self.assertFalse((self.member/prefix/'Helix.Desktop.exe').exists())

    def test_reject_desktop_data_redist_and_path_traversal(self):
        for name in ['desktop/../current.json','desktop/data/session.dpapi','desktop/redist/runtime.exe','desktop/current.json:stream','desktop/versions/v/data/token.json']:
            with self.assertRaises(U.UpdateError):U.safe_member(name)

    def test_incomplete_native_release_rejected(self):
        d=self.native(self.lead);(d/'WebView2Loader.dll').unlink();self.use(self.lead)
        with self.assertRaises(U.UpdateError):U.make_release('bad','3.2.0')

    def test_desktop_feed_preferred_but_legacy_still_available(self):
        self.use(self.lead);legacy=U.make_release('bridge','3.1.2');client=FakeGitHub();feed=GH.GitHubFeed('owner/repo',client)
        feed.publish(legacy['manifest'],legacy['raw'])
        self.native(self.lead);native=U.make_release('native','3.2.0')
        for name,raw in [('helix-desktop-update.json',json.dumps(native['manifest']).encode()),(native['manifest']['file'],native['raw'])]:
            aid=len(client.files)+1;client.files[aid]=raw;client.release['assets'].append({'id':aid,'name':name,'state':'uploaded'})
        actual,load=feed.fetch();self.assertEqual(actual,native['manifest']);self.assertEqual(load(),native['raw'])
        bridge=json.loads(feed._asset(next(a for a in client.release['assets'] if a['name']=='helix-update.json')))
        self.assertEqual(bridge['version'],'3.1.2')

# Reuse only the fixture, not inherited behavioral test cases.
for name in [n for n in vars(Updater) if n.startswith('test_')]:setattr(DesktopUpdates,name,None)
