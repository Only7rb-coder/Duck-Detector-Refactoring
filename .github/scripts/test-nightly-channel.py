#!/usr/bin/env python3
# Copyright 2026 Duck Apps Contributor
# If you have any questions, suggestions, or other inquiries, please email Eltavine <me@eltavine.com>.
# SPDX-License-Identifier: Apache-2.0
"""Run the actual channel transition with an in-memory GitHub transport and real files."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

import nightly_channel as channel
import nightly_release

SHA = 'a' * 40
TAG = 'nightly-2026-10-18-aaaaaaaa'
REPO = 'example/repository'


class GitHubFixture:
    def __init__(self):
        self.events = []
        self.fail_upload = None
        self.fail_ref = False
        self.archive = {'schemaVersion': 1, 'channel': 'nightly', 'branch': 'master', 'releaseTag': TAG,
                        'versionName': '2026.10.18-test', 'versionCode': 500, 'builtAtUtc': '2026-10-18T00:00:00Z', 'commit': {'sha': SHA}}
        self.files = {TAG: {}}
        for key, name in [('apk', 'build-' + TAG + '.apk'), ('sdk', 'sdk-' + TAG + '.aar')]:
            payload = (key + ' archive').encode()
            self.files[TAG][name] = payload
            self.archive[key] = {'name': name, 'sizeBytes': len(payload),
                                 'sha256': hashlib.sha256(payload).hexdigest(),
                                 'downloadUrl': f'https://github.com/{REPO}/releases/download/{TAG}/{name}'}
        self.files[TAG]['update.json'] = json.dumps(self.archive).encode()
        self.old = {'versionCode': 499, 'builtAtUtc': '2026-10-17T00:00:00Z'}
        self.files['nightly'] = {'old.apk': b'old apk', 'old.aar': b'old sdk', 'update.json': json.dumps(self.old).encode()}
        self.draft = {TAG: False, 'nightly': False}
        self.immutable = {TAG: True, 'nightly': False}
        self.refs = {TAG: SHA, 'nightly': 'b' * 40}

    def api(self, *args, **kwargs):
        self.events.append(args)
        if args[0] == 'api':
            if '-X' in args:
                if self.fail_ref:
                    self.fail_ref = False
                    raise RuntimeError('interrupted after pointer upload')
                self.refs['nightly'] = SHA
                return None
            endpoint = args[1]
            if '/git/ref/tags/' in endpoint:
                tag = endpoint.rsplit('/', 1)[1]
                return {'object': {'type': 'commit', 'sha': self.refs[tag]}} if tag in self.refs else None
            tag = endpoint.rsplit('/', 1)[1]
            if tag not in self.files and kwargs.get('missing_ok'):
                return None
            return {'tag_name': tag, 'prerelease': True, 'draft': self.draft[tag], 'immutable': self.immutable[tag],
                    'assets': [{'name': name, 'size': len(data), 'state': 'uploaded'}
                               for name, data in self.files[tag].items()]}
        if args[:2] == ('release', 'create'):
            self.files[args[2]] = {}
            self.draft[args[2]] = True
            return None
        if args[:2] == ('release', 'edit'):
            self.draft[args[2]] = False
            return None
        raise AssertionError(args)

    def run(self, args, **kwargs):
        self.events.append(tuple(args))
        operation, tag = args[2], args[3]
        if operation == 'download':
            name = args[args.index('--pattern') + 1]
            directory = Path(args[args.index('--dir') + 1])
            (directory / name).write_bytes(self.files[tag][name])
        elif operation == 'upload':
            path = Path(args[4])
            if self.fail_upload == path.name:
                self.fail_upload = None
                self.files[tag].pop(path.name, None)
                raise RuntimeError('upload failed after removing previous pointer')
            self.files[tag][path.name] = path.read_bytes()
        else:
            raise AssertionError(args)
        return CompletedProcess(args, 0)


class ChannelTest(unittest.TestCase):
    def setUp(self):
        self.fake = GitHubFixture()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for owner, name, effect in [(channel, 'gh', self.fake.api), (nightly_release, 'gh', self.fake.api),
                                     (channel.subprocess, 'run', self.fake.run)]:
            mock = patch.object(owner, name, side_effect=effect)
            mock.start()
            self.addCleanup(mock.stop)

    def advance(self):
        return channel.advance(REPO, TAG, SHA, Path(self.directory.name), 'Telegram.apk')

    def test_assets_are_verified_before_pointer_changes_and_history_is_preserved(self):
        original = deepcopy(self.fake.files[TAG])
        advanced, apk_path = self.advance()
        self.assertTrue(advanced)
        pointer = json.loads(self.fake.files['nightly']['update.json'])
        self.assertEqual('nightly', pointer['releaseTag'])
        self.assertIn('/download/nightly/', pointer['apk']['downloadUrl'])
        self.assertEqual({'tag': TAG, 'apk': self.fake.archive['apk']}, pointer['archive'])
        self.assertEqual(original, self.fake.files[TAG])
        self.assertIn('old.apk', self.fake.files['nightly'])
        self.assertIn('old.aar', self.fake.files['nightly'])
        uploads = [event[4] for event in self.fake.events if event[:3] == ('gh', 'release', 'upload')]
        self.assertEqual(['.apk', '.aar', '.json'], [Path(path).suffix for path in uploads])
        self.assertEqual(self.fake.files[TAG][self.fake.archive['apk']['name']], apk_path.read_bytes())
        self.assertEqual(SHA, self.fake.refs['nightly'])
        edits = [event for event in self.fake.events if event[:2] == ('release', 'edit')]
        self.assertTrue(edits)
        self.assertTrue(all(not any(flag.startswith('--draft') for flag in event) for event in edits))

    def test_unpublished_archive_cannot_advance_channel(self):
        self.fake.draft[TAG] = True
        before = deepcopy(self.fake.files['nightly'])
        with self.assertRaises(ValueError):
            self.advance()
        self.assertEqual(before, self.fake.files['nightly'])

    def test_corrupt_archive_stops_before_channel_pointer_changes(self):
        self.fake.files[TAG][self.fake.archive['apk']['name']] = b'wrong bytes'
        before = self.fake.files['nightly']['update.json']
        with self.assertRaises(ValueError):
            self.advance()
        self.assertEqual(before, self.fake.files['nightly']['update.json'])

    def test_asset_upload_failure_leaves_previous_manifest_usable(self):
        self.fake.fail_upload = self.fake.archive['sdk']['name']
        before = self.fake.files['nightly']['update.json']
        with self.assertRaises(RuntimeError):
            self.advance()
        self.assertEqual(before, self.fake.files['nightly']['update.json'])
        self.assertIn('old.apk', self.fake.files['nightly'])

    def test_pointer_upload_failure_restores_previous_manifest(self):
        self.fake.fail_upload = 'update.json'
        before = self.fake.files['nightly']['update.json']
        with self.assertRaises(RuntimeError):
            self.advance()
        self.assertEqual(before, self.fake.files['nightly']['update.json'])

    def test_older_build_cannot_roll_the_channel_back(self):
        newer = dict(self.fake.old, versionCode=501)
        self.fake.files['nightly']['update.json'] = json.dumps(newer).encode()
        self.assertEqual((False, None), self.advance())
        self.assertEqual(newer, json.loads(self.fake.files['nightly']['update.json']))
        self.assertFalse(any(event[:3] == ('gh', 'release', 'upload') for event in self.fake.events))

    def test_rerun_repairs_alias_ref_without_replacing_archive_or_pointer(self):
        self.fake.fail_ref = True
        with self.assertRaises(RuntimeError):
            self.advance()
        pointer = self.fake.files['nightly']['update.json']
        self.fake.events.clear()
        advanced, _ = self.advance()
        self.assertFalse(advanced)
        self.assertEqual(SHA, self.fake.refs['nightly'])
        self.assertEqual(pointer, self.fake.files['nightly']['update.json'])
        self.assertFalse(any(event[:3] == ('gh', 'release', 'upload') for event in self.fake.events))

    def test_missing_channel_is_never_recreated(self):
        del self.fake.files['nightly']
        before = deepcopy(self.fake.files[TAG])
        with self.assertRaisesRegex(ValueError, 'never recreate'):
            self.advance()
        self.assertEqual(before, self.fake.files[TAG])
        self.assertFalse(any(event[:2] == ('release', 'create') for event in self.fake.events))

    def test_locked_or_draft_channel_is_rejected_before_mutation(self):
        before = deepcopy(self.fake.files)
        for state, key in [(self.fake.immutable, 'immutable'), (self.fake.draft, 'draft')]:
            with self.subTest(key=key):
                state['nightly'] = True
                with self.assertRaisesRegex(ValueError, 'mutable nightly'):
                    self.advance()
                state['nightly'] = False
        self.assertEqual(before, self.fake.files)

    def test_mutable_archive_cannot_advance_channel(self):
        self.fake.immutable[TAG] = False
        before = deepcopy(self.fake.files['nightly'])
        with self.assertRaisesRegex(ValueError, 'must be immutable'):
            self.advance()
        self.assertEqual(before, self.fake.files['nightly'])

    def test_channel_asset_filename_collision_is_not_overwritten(self):
        self.fake.files['nightly'][self.fake.archive['apk']['name']] = b'another build'
        before = self.fake.files['nightly']['update.json']
        with self.assertRaises(ValueError):
            self.advance()
        self.assertEqual(before, self.fake.files['nightly']['update.json'])


if __name__ == '__main__':
    unittest.main()
