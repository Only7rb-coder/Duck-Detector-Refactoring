#!/usr/bin/env python3
# Copyright 2026 Duck Apps Contributor
# If you have any questions, suggestions, or other inquiries, please email Eltavine <me@eltavine.com>.
# SPDX-License-Identifier: Apache-2.0
"""Exercise immutable Nightly planning, publication and the actual workflow manifest builder."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from subprocess import CompletedProcess, run
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import nightly_release as release

SHA = 'a' * 40
TAG = 'nightly-2026-10-18-aaaaaaaa'
REPO = 'example/repository'


def complete(draft):
    return {'tag_name': TAG, 'draft': draft, 'immutable': not draft, 'prerelease': True, 'assets': [
        {'name': name, 'state': 'uploaded', 'size': 1, 'browser_download_url': f'https://github.com/{REPO}/releases/download/{TAG}/{name}'} for name in ['build.apk', 'sdk.aar', 'update.json']]}


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        mock = patch.object(release, 'require_channel')
        mock.start()
        self.addCleanup(mock.stop)

    def test_utc_tag_and_invalid_inputs(self):
        self.assertEqual(TAG, release.nightly_tag('2026-10-19T01:00:00+08:00', SHA))
        for stamp, sha in [('2026-02-30T00:00:00Z', SHA), ('2026-10-18T00:00:00', SHA),
                           ('2026-10-18T00:00:00Z', 'a' * 8)]:
            with self.assertRaises(ValueError):
                release.nightly_tag(stamp, sha)

    def test_existing_public_release_is_never_replaced(self):
        calls = []
        def api(*args, **kwargs):
            calls.append(args)
            if '/git/ref/' in args[1]:
                return {'object': {'type': 'commit', 'sha': SHA}}
            return complete(False)
        with patch.object(release, 'gh', side_effect=api):
            self.assertEqual((TAG, False), release.prepare(REPO, SHA, '2026-10-18T00:00:00Z'))
        self.assertTrue(all(call[0] == 'api' and '-X' not in call for call in calls))

    def test_unpublished_draft_can_be_rebuilt_without_deleting_its_tag(self):
        calls = []
        def api(*args, **kwargs):
            calls.append(args)
            if args[0] != 'api':
                return None
            if '/git/ref/' in args[1]:
                return {'object': {'type': 'commit', 'sha': SHA}}
            return complete(True)
        with patch.object(release, 'gh', side_effect=api):
            self.assertEqual((TAG, True), release.prepare(REPO, SHA, '2026-10-18T00:00:00Z'))
        self.assertIn(('release', 'delete', TAG, '--yes', '--repo', REPO), calls)
        self.assertFalse(any('--cleanup-tag' in call or 'force=true' in call for call in calls))

    def test_an_existing_tag_for_another_full_sha_fails_before_mutation(self):
        with patch.object(release, 'gh', return_value={'object': {'type': 'commit', 'sha': 'b' * 40}}) as api:
            with self.assertRaises(ValueError):
                release.prepare(REPO, SHA, '2026-10-18T00:00:00Z')
        self.assertEqual(1, api.call_count)

    def test_incomplete_public_release_is_not_overwritten(self):
        broken = complete(False)
        broken['assets'].pop()
        with patch.object(release, 'verify_ref'), patch.object(release, 'gh', return_value=broken) as api:
            with self.assertRaises(ValueError):
                release.prepare(REPO, SHA, '2026-10-18T00:00:00Z')
        self.assertEqual(1, api.call_count)

    def test_publish_requires_complete_draft_and_keeps_prerelease_status(self):
        state = complete(True)
        calls = []
        def api(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('release', 'edit'):
                state['draft'] = False
                state['immutable'] = True
                return None
            return deepcopy(state)
        with patch.object(release, 'verify_ref'), patch.object(release, 'gh', side_effect=api):
            release.publish(REPO, SHA, TAG)
        self.assertIn(('release', 'edit', TAG, '--draft=false', '--prerelease', '--latest=false', '--repo', REPO), calls)
        with patch.object(release, 'verify_ref'), patch.object(release, 'gh', return_value=complete(False)) as api:
            with self.assertRaises(ValueError):
                release.publish(REPO, SHA, TAG)
        self.assertEqual(1, api.call_count)

    def test_mutable_public_archive_cannot_be_reused(self):
        state = complete(False)
        state['immutable'] = False
        with patch.object(release, 'verify_ref'), patch.object(release, 'gh', return_value=state):
            with self.assertRaisesRegex(ValueError, 'must be immutable'):
                release.prepare(REPO, SHA, '2026-10-18T00:00:00Z')

    def test_publishing_without_repository_immutability_fails_closed(self):
        state = complete(True)
        def api(*args, **kwargs):
            if args[:2] == ('release', 'edit'):
                state['draft'] = False
                return None
            return deepcopy(state)
        with patch.object(release, 'verify_ref'), patch.object(release, 'gh', side_effect=api):
            with self.assertRaisesRegex(ValueError, 'must be immutable'):
                release.publish(REPO, SHA, TAG)

    def test_by_tag_404_falls_back_to_paginated_drafts(self):
        draft = complete(True)
        with patch.object(release, 'gh', side_effect=[None, [[], [draft]]]) as api:
            self.assertEqual(draft, release.find_release(REPO, TAG))
        self.assertEqual(('api', '--paginate', '--slurp', f'repos/{REPO}/releases?per_page=100'), api.call_args.args)

    def test_temporary_draft_urls_are_confined_to_the_official_repository(self):
        prefix = f'https://github.com/{REPO}/releases/download/'
        expected = prefix + TAG + '/build.apk'
        self.assertEqual(expected, release.canonical_asset_url(REPO, TAG, 'build.apk', prefix + 'untagged-abc123/build.apk'))
        self.assertEqual(expected, release.canonical_asset_url(REPO, TAG, 'build.apk', expected))
        for url in [expected + '?other=1', expected + '#other', expected.replace('github.com', 'evil.example'),
                    expected.replace(TAG, 'nightly'), expected.replace('build.apk', 'another.apk')]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                release.canonical_asset_url(REPO, TAG, 'build.apk', url)

    def test_only_404_is_treated_as_an_absent_release(self):
        with patch.object(release.subprocess, 'run', return_value=CompletedProcess([], 1, '', 'failed (HTTP 404)')):
            self.assertIsNone(release.gh('api', 'missing', missing_ok=True))
        with patch.object(release.subprocess, 'run', return_value=CompletedProcess([], 1, '', 'network unavailable')):
            with self.assertRaises(RuntimeError):
                release.gh('api', 'missing', missing_ok=True)

    def test_real_workflow_manifest_pins_both_artifacts_to_the_archive(self):
        workflow = (Path(__file__).resolve().parents[1] / 'workflows/build.yml').read_text()
        step = workflow.split('      - name: Prepare Nightly update manifest', 1)[1]
        source = textwrap.dedent(step.split("          python3 - <<'PY'\n", 1)[1].split('\n          PY', 1)[0])
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            def git(*args):
                return run(['git', *args], cwd=directory, check=True, text=True, capture_output=True).stdout.strip()
            git('init', '-q')
            git('-c', 'user.name=Release Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '--allow-empty', '-qm', 'Release fixture')
            sha = git('rev-parse', 'HEAD')
            tag = release.nightly_tag('2026-10-18T00:00:00Z', sha)
            (directory / 'nightly-changes.json').write_text(json.dumps({'baseTag': None, 'baseSha': None, 'totalCount': 0, 'entries': []}))
            assets = []
            paths = []
            for number, name in enumerate(['build.apk', 'sdk.aar'], 1):
                path = directory / name
                path.write_bytes(f'artifact {number}'.encode())
                paths.append(path)
                assets.append({'id': number, 'name': name, 'state': 'uploaded', 'size': path.stat().st_size,
                               'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest(),
                               'browser_download_url': f'https://github.com/{REPO}/releases/download/untagged-2a1517dcdbf3c6e04686/{name}'})
            env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parent), APK_PATH=str(paths[0]), AAR_PATH=str(paths[1]), GITHUB_REPOSITORY=REPO,
                       GITHUB_OUTPUT=str(directory / 'outputs'), NIGHTLY_TAG=tag, RELEASE_ASSETS_JSON=json.dumps(assets),
                       VERSION_NAME='2026.10.18-test', VERSION_CODE='500', NIGHTLY_MANIFEST_BRANCH='master',
                       BUILD_TIME_ISO='2026-10-18T00:00:00Z')
            result = run(['python3', '-c', source], cwd=directory, env=env, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            manifest = json.loads((directory / 'update.json').read_text())
            self.assertEqual(tag, manifest['releaseTag'])
            self.assertEqual(sha, manifest['commit']['sha'])
            self.assertEqual(f'https://github.com/{REPO}/releases/download/{tag}/build.apk', manifest['apk']['downloadUrl'])
            self.assertEqual(f'https://github.com/{REPO}/releases/download/{tag}/sdk.aar', manifest['sdk']['downloadUrl'])
            self.assertEqual(assets[1]['digest'][7:], manifest['sdk']['sha256'])


if __name__ == '__main__':
    unittest.main()
