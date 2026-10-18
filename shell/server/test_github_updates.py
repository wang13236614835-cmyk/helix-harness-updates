"""Private release transport: actual signatures, scoped credentials and interrupted publication."""
import json
import unittest
import urllib.request
from unittest.mock import patch

import updater as U
import github_updates as GH
import test_updater as UF


class FakeGitHub:
    def __init__(self):
        self.release = None
        self.files = {}
        self.calls = []
        self.fail_manifest = False

    def request(self, method, path, body=None, **kw):
        self.calls.append((method, path, kw))
        if path.endswith('/releases/latest'):
            return self.release if self.release and not self.release['draft'] else None
        if '/releases/tags/' in path:
            return self.release
        if method == 'POST' and path.endswith('/releases'):
            self.release = {'id': 10, 'draft': True, 'assets': [], 'html_url': 'https://github.com/owner/repo/releases/tag/v3.1.1'}
            return self.release
        if method == 'POST' and '/assets?name=' in path:
            from urllib.parse import unquote
            name = unquote(path.split('name=')[1])
            if name == 'helix-update.json' and self.fail_manifest:
                raise U.UpdateError('network interrupted')
            aid = len(self.files) + 1
            self.files[aid] = body
            self.release['assets'].append({'id': aid, 'name': name, 'state': 'uploaded'})
            return {'id': aid}
        if '/releases/assets/' in path:
            return self.files[int(path.rsplit('/', 1)[1])]
        if method == 'PATCH' and '/releases/10' in path:
            self.release.update(body)
            return self.release
        if method == 'GET' and path == '/repos/owner/repo':
            return {'full_name': 'owner/repo', 'private': True}
        raise AssertionError((method, path))


class GitHubUpdates(UF.Updater):
    def test_private_round_trip_keeps_project_data(self):
        self.use(self.lead)
        release = U.make_release('GitHub channel', '3.1.1')
        client = FakeGitHub()
        feed = GH.GitHubFeed('owner/repo', client)
        feed.publish(release['manifest'], release['raw'])
        self.assertFalse(client.release['draft'])
        manifest, loader = feed.fetch()
        self.use(self.member)
        U.stage(manifest, loader())
        U.apply_staged(self.member)
        self.assertEqual(U.confirm_or_rollback(self.member, True), 'ok')
        self.assertIn('OLD', (self.member / 'shell/data/people.json').read_text())

    def test_incomplete_publish_stays_draft_and_resumes(self):
        self.use(self.lead); release = U.make_release('resume', '3.1.1')
        client = FakeGitHub(); client.fail_manifest = True
        feed = GH.GitHubFeed('owner/repo', client)
        with self.assertRaises(U.UpdateError):
            feed.publish(release['manifest'], release['raw'])
        self.assertTrue(client.release['draft']); self.assertIsNone(feed.fetch())
        client.fail_manifest = False
        feed.publish(release['manifest'], release['raw'])
        self.assertEqual(len(client.files), 2)
        self.assertEqual(sum(p.endswith('/releases') and m == 'POST' for m,p,k in client.calls), 1)
        self.assertFalse(client.release['draft'])

    def test_published_content_never_overwritten(self):
        self.use(self.lead); release = U.make_release('original', '3.1.1')
        client = FakeGitHub(); feed = GH.GitHubFeed('owner/repo', client)
        feed.publish(release['manifest'], release['raw'])
        client.files[1] = b'changed'
        with self.assertRaises(U.UpdateError):
            feed.publish(release['manifest'], release['raw'])
        self.assertFalse(any(m == 'DELETE' for m,p,k in client.calls))

    def test_configuration_encrypted_and_status_redacted(self):
        self.use(self.member); token = 'test-github-credential-secret-0123456789'
        with patch.object(GH.Client, 'request', return_value={'full_name':'owner/repo','private':True}):
            result = GH.configure({'repository':'owner/repo', 'token':token})
        self.assertTrue(result['connected'])
        self.assertNotIn(token, GH._path().read_text())
        self.assertNotIn(token, json.dumps(U.status()))
        self.assertEqual(GH._secret(GH._config()), token)
        self.assertEqual(U.settings()['feeds'], ['github:owner/repo'])

    def test_failed_connection_keeps_previous_credential(self):
        self.use(self.member)
        with patch.object(GH.Client, 'request', return_value={'full_name':'owner/repo','private':True}):
            GH.configure({'repository':'owner/repo', 'token':'previous-credential-0123456789'})
        before = GH._path().read_bytes()
        with patch.object(GH.Client, 'request', side_effect=U.UpdateError('permission denied')):
            with self.assertRaises(U.UpdateError):
                GH.configure({'repository':'owner/repo', 'token':'wrong-credential'})
        self.assertEqual(GH._path().read_bytes(), before)

    def test_changing_repository_updates_preset_channel(self):
        self.use(self.member)
        U._write_json(U.TRUST, {'feeds':[GH.DEFAULT_FEED]})
        with patch.object(GH.Client, 'request', return_value={'full_name':'owner/repo','private':True}):
            GH.configure({'repository':'owner/repo', 'token':'repository-credential-0123456789'})
        self.assertEqual(U.settings()['feeds'], ['github:owner/repo'])

    def test_notion_default_migrates_without_changing_custom_feed(self):
        self.use(self.member)
        U.save_settings({'feeds':['notion'], 'publish_notion':True})
        self.assertEqual(U.settings()['feeds'], [GH.DEFAULT_FEED])
        self.assertFalse(U.settings()['publish_notion'])
        U.save_settings({'feeds':['https://updates.example/helix-update.json']})
        self.assertEqual(U.settings()['feeds'], ['https://updates.example/helix-update.json'])
        U._write_json(U.TRUST, {'feeds':[GH.DEFAULT_FEED]})
        self.assertEqual(U.settings()['feeds'], ['https://updates.example/helix-update.json'])

    def test_repository_and_redirect_reject_credential_leaks(self):
        for bad in ('https://evil.example/a/b', '../repo', 'https://github.com@evil.example/a/b', 'a/b?token=secret'):
            with self.assertRaises(U.UpdateError):
                GH.repository(bad)
        req = urllib.request.Request('https://api.github.com/repos/a/b/releases/assets/1', headers={'Authorization':'Bearer secret'})
        r = GH._Redirect().redirect_request(req, None, 302, '', {}, 'https://release-assets.githubusercontent.com/blob?sig=value')
        self.assertIsNone(r.get_header('Authorization'))
        with self.assertRaises(U.UpdateError):
            GH._Redirect().redirect_request(req, None, 302, '', {}, 'https://evil.example/blob')

    def test_no_releases_and_missing_manifest_are_distinct(self):
        client = FakeGitHub(); feed = GH.GitHubFeed('owner/repo', client)
        self.assertIsNone(feed.fetch())
        client.release = {'draft':False,'assets':[]}
        with self.assertRaises(U.UpdateError):
            feed.fetch()

    def test_member_can_connect_but_cannot_publish_or_change_publish_settings(self):
        import test_source_project as fixture
        f = fixture.SourceProjectAPI(); f.setUp()
        try:
            peer = f.s.post('/api/auth/register', {'name':'github-peer','pin':'peer-password-123','pin_confirm':'peer-password-123'})
            self.assertTrue(peer['ok'], peer)
            peer = f.s.post('/api/auth/login', {'username':'github-peer','pin':'peer-password-123'})
            self.assertTrue(peer['ok'], peer)
            denied = f.s.post('/api/update/publish', {'notes':'unauthorized'}, peer['token'])
            self.assertFalse(denied['ok'])
            result = f.s.post('/api/update/settings', {'publish_github':False}, peer['token'])
            self.assertTrue(result['status']['settings']['publish_github'])
            connect = f.s.post('/api/update/github', {'repository':'bad repo','token':'unused'}, peer['token'])
            self.assertIn('仓库', connect.get('error',''))
        finally:
            f.tearDown()


for _name in [n for n in vars(UF.Updater) if n.startswith('test_')]:
    setattr(GitHubUpdates, _name, None)

if __name__ == '__main__':
    unittest.main(verbosity=2)
