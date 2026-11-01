"""Standalone synthetic tests: no app imports, credentials, network or installed writes."""
import ast
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

import helix_sync
import helix_sync_metadata as M
import helix_remote as R


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.pid = 'P-test'
        self.root = self.base / 'projects' / self.pid
        self.root.mkdir(parents=True)
        self.P = types.SimpleNamespace(pdir=lambda pid: self.base / 'projects' / pid,
                                       is_local=lambda pid: (self.base / 'projects' / pid / 'project.json').is_file())
        self.project = {'id': self.pid, 'name': 'Original', 'question': 'Q',
                        'weeks': [{'week': 1, 'start': '2026-11-01', 'end': '2026-11-07', 'theme': 'Plan'}],
                        'tasks': [{'task_id': 'T1', 'week': 1, 'owner': 'A', 'cross_reviewer': 'B',
                                   'status': 'PENDING', 'progress': 0, 'depends_on': [], 'private_extension': {'a': 1}},
                                  {'task_id': 'T2', 'week': 1, 'owner': 'B', 'cross_reviewer': 'A', 'status': 'DONE'}],
                        'members': [{'name': 'A'}, {'name': 'B'}, {'name': 'C'}],
                        'duties': [{'key': 'research'}], 'source': {'library': '/cloud/library', 'roots': {}},
                        'local_team': {'seats': [{'person_id': 'cloud-owner'}], 'source_roots': {'x': '/cloud/x'}},
                        'sync_import': {'live': 'preserve'}, 'claim_ceiling': 'EXPLORATORY_ONLY'}
        self.write_project()
        self.ledger = self.root / 'helix' / self.pid / 'ledger.jsonl'
        self.ledger.parent.mkdir(parents=True)
        self.ledger.write_text(json.dumps({'type': 'task.checkin', 'payload': {'task_id': 'T2'}}) + '\n')
        (self.root / 'seat_claims.json').write_text('{"seat":"cloud-account"}')
        M.PENDING.clear()
        R.PENDING.clear()
        self.env = patch.dict(os.environ, {}, clear=False)
        self.env.start()
        os.environ.pop('HELIX_CLOUD_MODE', None)
        self.addCleanup(self.env.stop)

    def write_project(self):
        (self.root / 'project.json').write_text(json.dumps(self.project), encoding='utf-8')

    def request(self, delta=None, files=None):
        return {'source_project': self.pid, 'target': self.pid,
                'expected_revision': helix_sync.revision(self.P, self.pid),
                'patch': delta if delta is not None else {'name': 'New'}, 'files': files or {}}

    def preview(self, body=None):
        return M.preview(self.P, body or self.request(), owner='cloud-lead')

    def commit(self, preview, **extra):
        body = {'token': preview['token'], 'source_project': self.pid, 'target': self.pid,
                'expected_revision': preview['expected_revision'], 'confirm': True}
        body.update(extra)
        return M.apply(self.P, body, owner='cloud-lead')

    def test_scoped_apply_preserves_every_other_file_and_identity(self):
        untouched = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file() and p.name != 'project.json'}
        preview = self.preview(self.request({'direction': 'Updated direction', 'research_progress': {'summary': 'Synthetic', 'counts': {'papers': 2}},
                                             'tasks': [{'task_id': 'T1', 'start': '2026-11-02', 'end': '2026-11-06'}]},
                                            {'updates/report.md': '# Evidence\n', 'updates/report.json': '{"papers":2}', 'research-progress.json': '{"summary":"Synthetic"}'}))
        result = self.commit(preview)
        self.assertTrue(result['metadata_only'])
        current = json.loads((self.root / 'project.json').read_text())
        for key in ('source', 'local_team', 'sync_import', 'members', 'claim_ceiling'):
            self.assertEqual(current[key], self.project[key])
        for old, new in zip(self.project['tasks'], current['tasks']):
            self.assertEqual(old['task_id'], new['task_id'])
            self.assertEqual(old['status'], new['status'])
        self.assertEqual(current['tasks'][0]['private_extension'], {'a': 1})
        for rel, raw in untouched.items():
            self.assertEqual((self.root / rel).read_bytes(), raw)
        self.assertEqual(helix_sync.revision(self.P, self.pid), result['revision'])
        backup = self.base / '.helix-sync' / result['backup']
        self.assertTrue((backup / 'transaction.json').is_file())

    def test_duplicate_commit_survives_token_cache_loss(self):
        preview = self.preview()
        first = self.commit(preview)
        M.PENDING.clear()
        (self.root / 'unrelated.txt').write_text('later independent work')
        self.assertEqual(first, self.commit(preview))
        self.assertEqual((self.root / 'unrelated.txt').read_text(), 'later independent work')

    def test_conflict_on_any_project_file(self):
        preview = self.preview()
        self.ledger.write_text(self.ledger.read_text() + '{}\n')
        with self.assertRaises(M.Conflict):
            self.commit(preview)
        self.assertEqual(json.loads((self.root / 'project.json').read_text())['name'], 'Original')

    def test_wrong_baseline_and_project_identity(self):
        for body in (dict(self.request(), expected_revision='0' * 64),
                     dict(self.request(), target='P-other'), dict(self.request(), source_project='P-other')):
            with self.assertRaises((M.MetadataError, helix_sync.SyncError)):
                self.preview(body)

    def test_unknown_project_task_week_and_top_fields(self):
        for delta in ({'source': {}}, {'local_team': {}}, {'status': 'DONE'}, {'members': []},
                      {'tasks': [{'task_id': 'T1', 'person_id': 'fake'}]},
                      {'weeks': [{'week': 1, 'injected': True}]}):
            with self.assertRaises(M.MetadataError):
                self.preview(self.request(delta))
        with self.assertRaises(M.MetadataError):
            self.preview(dict(self.request(), owner='fake'))

    def test_status_and_progress_cannot_change(self):
        for delta in ({'status': 'DONE'}, {'status': 'pending'}, {'progress': 1}, {'execution_mode': 'agent'}, {'depends_on': ['T2']}):
            with self.assertRaises(M.MetadataError):
                self.preview(self.request({'tasks': [{'task_id': 'T1', **delta}]}))
        preview = self.preview(self.request({'tasks': [{'task_id': 'T1', 'status': 'PENDING', 'progress': 0, 'start': '2026-11-02'}]}))
        self.commit(preview)

    def test_unknown_duplicate_task_ids(self):
        for rows in ([{'task_id': 'new'}], [{'task_id': 'T1'}, {'task_id': 'T1'}]):
            with self.assertRaises(M.MetadataError):
                self.preview(self.request({'tasks': rows}))

    def test_reassign_pending_only_to_existing_distinct_members(self):
        preview = self.preview(self.request({'tasks': [{'task_id': 'T1', 'owner': 'C'}]}))
        self.commit(preview)
        self.assertEqual(json.loads((self.root / 'project.json').read_text())['tasks'][0]['owner'], 'C')
        for task in ({'task_id': 'T2', 'owner': 'C'}, {'task_id': 'T1', 'owner': 'B'}, {'task_id': 'T1', 'owner': 'unknown'}):
            with self.assertRaises(M.MetadataError):
                self.preview(self.request({'tasks': [task]}))

    def test_work_event_locks_assignment_even_pending(self):
        self.ledger.write_text(json.dumps({'payload': {'task_id': 'T1'}, 'type': 'human.execution.started'}) + '\n')
        with self.assertRaises(M.MetadataError):
            self.preview(self.request({'tasks': [{'task_id': 'T1', 'owner': 'C'}]}))

    def test_corrupt_ledger_fails_closed_for_assignment(self):
        self.ledger.write_text('not json\n')
        with self.assertRaises(M.MetadataError):
            self.preview(self.request({'tasks': [{'task_id': 'T1', 'owner': 'C'}]}))

    def test_seat_mapping_protected(self):
        self.project['tasks'][0]['workspace_slot'] = 's1'
        self.write_project()
        with self.assertRaises(M.MetadataError):
            self.preview(self.request({'tasks': [{'task_id': 'T1', 'owner': 'C'}]}))

    def test_invalid_dates_weeks_and_paths(self):
        for delta in ({'tasks': [{'task_id': 'T1', 'start': 'bad'}]}, {'tasks': [{'task_id': 'T1', 'week': 2}]},
                      {'weeks': []}, {'weeks': [{'week': 1, 'start': '2026-11-03', 'end': '2026-11-01'}]}):
            with self.assertRaises(M.MetadataError):
                self.preview(self.request(delta))
        for rel in ('project.json', '../x.md', 'updates/../x.md', 'updates/a/b.md', 'updates/CON.md',
                    'updates/x.py', 'helix/P-test/ledger.jsonl', 'updates/x.md:stream'):
            with self.assertRaises((M.MetadataError, helix_sync.SyncError)):
                self.preview(self.request(files={rel: 'x'}))

    def test_request_bound_and_json_validation(self):
        with self.assertRaises(M.MetadataError):
            self.preview(self.request(files={'updates/report.md': 'x' * M.MAX_REQUEST}))
        for text in ('invalid', '[]', '{"count":NaN}'):
            with self.assertRaises(M.MetadataError):
                self.preview(self.request(files={'updates/report.json': text}))
        with self.assertRaises(M.MetadataError):
            self.preview(self.request({'research_progress': {'n': float('nan')}}))

    def test_owner_confirmation_and_changed_retry(self):
        preview = self.preview()
        body = {'token': preview['token'], 'source_project': self.pid, 'target': self.pid,
                'expected_revision': preview['expected_revision'], 'confirm': True}
        with self.assertRaises(M.Conflict):
            M.apply(self.P, body, owner='different-local-id')
        with self.assertRaises(M.MetadataError):
            self.commit(preview, confirm=False)
        self.commit(preview)
        with self.assertRaises(M.Conflict):
            self.commit(preview, expected_revision='0' * 64)

    def test_rollback_on_partial_write_and_revision_failure(self):
        for failure in ('write', 'revision'):
            with self.subTest(failure=failure):
                before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                preview = self.preview(self.request(files={'updates/report.md': 'new'}))
                atomic = M._atomic
                revision = helix_sync.revision
                failed = [False]
                def atomic_fail(path, raw):
                    if path == self.root / 'project.json' and not failed[0] and failure == 'write':
                        failed[0] = True
                        raise OSError('synthetic write failure')
                    return atomic(path, raw)
                def revision_fail(P, pid):
                    if failure == 'revision' and (self.root / 'updates/report.md').exists() and not failed[0]:
                        failed[0] = True
                        raise OSError('synthetic post-write revision failure')
                    return revision(P, pid)
                with patch.object(M, '_atomic', atomic_fail), patch.object(helix_sync, 'revision', revision_fail):
                    with self.assertRaises(OSError):
                        self.commit(preview)
                after = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                self.assertEqual(before, after)
                self.assertFalse((self.root / 'updates').exists())
                with self.assertRaises(M.Conflict):
                    self.commit(preview)

    def test_recover_interrupted_transaction_before_new_preview(self):
        before = (self.root / 'project.json').read_bytes()
        preview = self.preview()
        token = preview['token']
        folder = self.base / '.helix-sync' / ('metadata-' + self.pid + '-' + token)
        folder.mkdir()
        (folder / '0.bak').write_bytes(before)
        journal = {'state': 'writing', 'files': [{'path': 'project.json', 'existed': True, 'backup': '0.bak'}], 'new_dirs': []}
        (folder / 'transaction.json').write_text(json.dumps(journal))
        (self.root / 'project.json').write_text('{"id":"P-test","name":"partial"}')
        self.preview(dict(self.request(), expected_revision=preview['expected_revision']))
        self.assertEqual((self.root / 'project.json').read_bytes(), before)
        self.assertEqual(json.loads((folder / 'transaction.json').read_text())['state'], 'rolled_back')

    def test_existing_summary_restored_on_failure(self):
        (self.root / 'updates').mkdir()
        report = self.root / 'updates/report.md'
        report.write_bytes(b'original summary\r\n')
        before = helix_sync.revision(self.P, self.pid)
        preview = self.preview(self.request(files={'updates/report.md': 'replacement'}))
        atomic = M._atomic
        failed = [False]
        def fail_once(path, raw):
            if path == self.root / 'project.json' and not failed[0]:
                failed[0] = True
                raise OSError('synthetic failure after report replacement')
            return atomic(path, raw)
        with patch.object(M, '_atomic', fail_once):
            with self.assertRaises(OSError):
                self.commit(preview)
        self.assertEqual(report.read_bytes(), b'original summary\r\n')
        self.assertEqual(helix_sync.revision(self.P, self.pid), before)

    def test_recovery_does_not_touch_project_with_prefix_id(self):
        work = self.base / '.helix-sync'
        work.mkdir()
        other = work / ('metadata-' + self.pid + '-other-' + 'a' * 32)
        other.mkdir()
        journal = {'state': 'writing', 'files': [{'path': 'project.json', 'existed': False, 'backup': '0.bak'}]}
        (other / 'transaction.json').write_text(json.dumps(journal))
        self.preview()
        self.assertEqual(json.loads((self.root / 'project.json').read_text())['id'], self.pid)
        self.assertEqual(json.loads((other / 'transaction.json').read_text())['state'], 'writing')

    def test_symlink_destination_refused(self):
        target = self.base / 'external.md'
        target.write_text('untouched')
        body = self.request(files={'updates/report.md': 'bad'})
        (self.root / 'updates').mkdir()
        try:
            (self.root / 'updates/report.md').symlink_to(target)
        except OSError:
            self.skipTest('OS account cannot create symlinks')
        with self.assertRaises((M.MetadataError, helix_sync.SyncError)):
            self.preview(body)
        self.assertEqual(target.read_text(), 'untouched')

    def test_concurrent_apply_only_commits_once(self):
        preview = self.preview()
        results, errors = [], []
        def run():
            try:
                results.append(self.commit(preview))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(item == results[0] for item in results))

    def bridge(self):
        actor = {'id': 'local-lead', 'role': 'lead'}
        S = types.SimpleNamespace(P=self.P, need_person=lambda h: actor, need_admin=lambda h: actor,
                                  IDENT=types.SimpleNamespace(store_lock=threading.RLock()))
        handler = types.SimpleNamespace(headers={'Host': '127.0.0.1:18773', 'Origin': 'http://127.0.0.1:18773'},
                                        server=types.SimpleNamespace(server_port=18773))
        return S, handler, actor

    def test_bridge_contract_owner_origin_and_session_bound(self):
        S, handler, actor = self.bridge()
        calls = []
        config = {'origin': 'https://example.invalid', 'cookie': 'synthetic-session'}
        def request(base, path, body, cookie):
            calls.append((path, copy.deepcopy(body)))
            self.assertNotIn('owner', body)
            if path.endswith('metadata-preview'):
                return {'project_id': self.pid, 'expected_revision': body['expected_revision'], 'token': 'a' * 32}
            return {'id': self.pid, 'metadata_only': True}
        with patch.object(R, 'load', return_value=config), patch.object(R, 'request', side_effect=request):
            result = R.handle(S, handler, 'metadata-preview', {'project': self.pid, 'expected_revision': 'b' * 64, 'patch': {'name': 'cloud'}})
            self.assertEqual(R.handle(S, handler, 'metadata-apply', {'transfer': result['transfer'], 'confirm': True})['id'], self.pid)
            self.assertEqual(len(calls), 2)
            R.handle(S, handler, 'metadata-apply', {'transfer': result['transfer'], 'confirm': True})
            self.assertEqual(len(calls), 2)
            config['cookie'] = 'changed-session'
            with self.assertRaises(R.RemoteError):
                R.handle(S, handler, 'metadata-apply', {'transfer': result['transfer'], 'confirm': True})
            actor['role'] = 'member'
            with self.assertRaises(PermissionError):
                R.handle(S, handler, 'metadata-preview', {})
            handler.headers['Origin'] = 'https://hostile.invalid'
            with self.assertRaises(PermissionError):
                R.handle(S, handler, 'metadata-preview', {})


    def test_real_staged_post_routes_without_importing_app(self):
        # Compile the actual handler method only; importing serve initializes app state.
        source = (Path(__file__).parent / 'serve.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'H')
        methods = [node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name in ('do_POST', '_body_json')]
        module = ast.Module(body=[ast.ClassDef(name='Handler', bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
        ast.fix_missing_locations(module)
        from urllib.parse import urlparse, parse_qs
        class AuthError(Exception):
            pass
        ident = types.SimpleNamespace(store_lock=threading.RLock(), status=lambda _: {'bootstrap': True})
        person = {'id': 'cloud-lead', 'role': 'lead'}
        env = {'os': os, 'json': json, 'time': __import__('time'), 'urlparse': urlparse, 'parse_qs': parse_qs,
               'LAST_WRITE': [0], 'PUBLIC_POST': set(), 'IDENT': ident, 'P': self.P, 'load_people': lambda: [],
               'need_admin': lambda _: person, 'ID': types.SimpleNamespace(AuthError=AuthError)}
        exec(compile(module, '<staged-post-handler>', 'exec'), env)
        def call(path, body, cloud=False, origin=None, length=None):
            h = env['Handler']()
            h.path = path
            raw = json.dumps(body).encode()
            h.headers = {'X-Helix': '1', 'Host': '127.0.0.1:18773', 'Content-Length': str(len(raw) if length is None else length)}
            if origin:
                h.headers['Origin'] = origin
            h.server = types.SimpleNamespace(server_port=18773)
            h.rfile = io.BytesIO(raw)
            h._json = lambda obj, status=200: (status, obj)
            with patch.dict(os.environ, {'HELIX_CLOUD_MODE': '1' if cloud else '0'}):
                return h.do_POST()
        status, preview = call('/api/desktop-sync/metadata-preview', self.request(), cloud=True)
        self.assertEqual(status, 200)
        status, error = call('/api/desktop-sync/metadata-apply', {'token': preview['token'], 'source_project': self.pid, 'target': self.pid,
                                                               'expected_revision': '0' * 64, 'confirm': True}, cloud=True)
        self.assertEqual(status, 409)
        self.assertFalse(error['ok'])
        self.assertEqual(call('/api/projects/metadata-preview', self.request(), origin='https://hostile.invalid')[0], 403)
        self.assertEqual(call('/api/projects/metadata-preview', self.request(), length=M.MAX_REQUEST + 1)[0], 413)
        person['role'] = 'member'
        self.assertEqual(call('/api/desktop-sync/metadata-preview', self.request(), cloud=True)[0], 403)
        person['role'] = 'lead'
        local_status, local_preview = call('/api/projects/metadata-preview', self.request())
        self.assertEqual(local_status, 200)
        self.assertIn('token', local_preview)
        commit = {'token': local_preview['token'], 'source_project': self.pid, 'target': self.pid,
                  'expected_revision': local_preview['expected_revision'], 'confirm': True}
        self.assertEqual(call('/api/projects/metadata-apply', dict(commit, owner='spoofed'))[0], 400)
        commit_status, committed = call('/api/projects/metadata-apply', commit)
        self.assertEqual(commit_status, 200)
        self.assertTrue(committed['metadata_only'])
        self.assertEqual(call('/api/projects/metadata-apply', commit)[1]['revision'], committed['revision'])


if __name__ == '__main__':
    unittest.main()
