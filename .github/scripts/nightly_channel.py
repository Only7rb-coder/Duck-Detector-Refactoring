#!/usr/bin/env python3
# Copyright 2026 Duck Apps Contributor
# If you have any questions, suggestions, or other inquiries, please email Eltavine <me@eltavine.com>.
# SPDX-License-Identifier: Apache-2.0
"""Advance the fixed Nightly channel after its immutable archive is public and verified."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from nightly_release import gh, require_channel, require_complete, require_immutable, validate_tag, verify_ref


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9._+-]+', name):
        raise ValueError('Unsafe release asset filename')
    return name


def download(repo, tag, name, directory):
    safe_name(name)
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(['gh', 'release', 'download', tag, '--repo', repo,
                    '--pattern', name, '--dir', str(directory), '--clobber'], check=True)
    return directory / name


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def validate_manifest(manifest, repo, tag, sha):
    validate_tag(tag, sha)
    if (type(manifest.get('schemaVersion')) is not int or manifest['schemaVersion'] != 1 or manifest.get('channel') != 'nightly'
            or manifest.get('branch') not in ['master', 'main'] or manifest.get('commit', {}).get('sha') != sha
            or manifest.get('releaseTag') != tag):
        raise ValueError('Archive manifest identifies another build')
    version = manifest.get('versionCode')
    if not isinstance(manifest.get('versionName'), str) or not manifest['versionName'].strip():
        raise ValueError('Archive version name is missing')
    if type(version) is not int or version <= 0:
        raise ValueError('Invalid archive version code')
    timestamp = datetime.fromisoformat(manifest['builtAtUtc'].replace('Z', '+00:00'))
    if timestamp.utcoffset() is None or timestamp.astimezone(timezone.utc).strftime('%Y-%m-%d') != tag[8:18]:
        raise ValueError('Archive tag and build date differ')
    for key, suffix in [('apk', '.apk'), ('sdk', '.aar')]:
        asset = manifest[key]
        name = safe_name(asset['name'])
        if (not name.endswith(suffix) or type(asset.get('sizeBytes')) is not int or asset['sizeBytes'] <= 0
                or not re.fullmatch('[0-9a-f]{64}', asset.get('sha256', ''))
                or asset.get('downloadUrl') != f'https://github.com/{repo}/releases/download/{tag}/{name}'):
            raise ValueError('Archive asset metadata is invalid')
    if manifest['apk']['sizeBytes'] > 256 * 1024 * 1024:
        raise ValueError('APK exceeds the independent download cap')


def alias_manifest(archive, repo, tag):
    channel = json.loads(json.dumps(archive))
    channel['releaseTag'] = 'nightly'
    channel['archive'] = {'tag': tag, 'apk': archive['apk']}
    for key in ['apk', 'sdk']:
        channel[key]['downloadUrl'] = f'https://github.com/{repo}/releases/download/nightly/{archive[key]["name"]}'
    return channel


def ordering(manifest):
    version = manifest.get('versionCode')
    if type(version) is not int or version <= 0:
        raise ValueError('Channel manifest has an invalid version code')
    instant = datetime.fromisoformat(manifest['builtAtUtc'].replace('Z', '+00:00'))
    if instant.utcoffset() is None:
        raise ValueError('Channel build time has no timezone')
    return version, instant


def upload_asset(repo, release, asset, path, directory):
    existing = [item for item in release.get('assets', []) if item.get('name') == asset['name']]
    if existing:
        if len(existing) != 1:
            raise ValueError('Duplicate channel asset names')
        check_file(download(repo, 'nightly', asset['name'], directory / 'existing'), asset)
    else:
        # Unique per-build filenames avoid replacing a file an older client is downloading.
        subprocess.run(['gh', 'release', 'upload', 'nightly', str(path), '--repo', repo], check=True)
        check_file(download(repo, 'nightly', asset['name'], directory / 'verify-assets'), asset)


def check_file(path, asset):
    if path.stat().st_size != asset['sizeBytes'] or digest(path) != asset['sha256']:
        raise ValueError(f'Asset differs from its archive manifest: {path.name}')


def write_pointer(repo, path, previous, directory):
    try:
        subprocess.run(['gh', 'release', 'upload', 'nightly', str(path), '--clobber', '--repo', repo], check=True)
        uploaded = download(repo, 'nightly', 'update.json', directory / 'verify-pointer')
        if uploaded.read_bytes() != path.read_bytes():
            raise ValueError('Published channel manifest differs from the prepared manifest')
    except Exception:
        if previous is not None:
            subprocess.run(['gh', 'release', 'upload', 'nightly', str(previous), '--clobber', '--repo', repo], check=True)
        raise


def move_channel_ref(repo, sha):
    endpoint = f'repos/{repo}/git/refs/tags/nightly'
    if gh('api', f'repos/{repo}/git/ref/tags/nightly', missing_ok=True) is None:
        gh('api', '-X', 'POST', f'repos/{repo}/git/refs', '-f', 'ref=refs/tags/nightly', '-f', f'sha={sha}')
    else:
        gh('api', '-X', 'PATCH', endpoint, '-f', f'sha={sha}', '-F', 'force=true')
    verify_ref(repo, 'nightly', sha)


def advance(repo, tag, sha, directory, telegram_name):
    if (not isinstance(telegram_name, str) or Path(telegram_name).name != telegram_name
            or '\\' in telegram_name or not telegram_name.endswith('.apk')
            or any(ord(character) < 32 or ord(character) == 127 for character in telegram_name)):
        raise ValueError('Invalid Telegram APK filename')
    validate_tag(tag, sha)
    release = require_channel(repo)
    verify_ref(repo, tag, sha)
    archive_release = gh('api', f'repos/{repo}/releases/tags/{tag}')
    require_complete(archive_release, tag)
    require_immutable(archive_release)
    archive_path = download(repo, tag, 'update.json', directory / 'archive')
    archive = json.loads(archive_path.read_text(encoding='utf-8'))
    validate_manifest(archive, repo, tag, sha)
    previous = None
    old = None
    if any(item.get('name') == 'update.json' for item in release.get('assets', [])):
        previous = download(repo, 'nightly', 'update.json', directory / 'previous')
        old = json.loads(previous.read_text(encoding='utf-8'))
        if ordering(old) > ordering(archive):
            return False, None
    files = {}
    for key in ['apk', 'sdk']:
        asset = archive[key]
        path = download(repo, tag, asset['name'], directory / 'archive')
        check_file(path, asset)
        upload_asset(repo, release, asset, path, directory)
        files[key] = path
    alias = alias_manifest(archive, repo, tag)
    unchanged = old == alias
    if not unchanged:
        pointer = directory / 'channel' / 'update.json'
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(json.dumps(alias, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        write_pointer(repo, pointer, previous, directory)
    notes = directory / 'channel-note.md'
    notes.write_text(
        f"Latest Nightly: [{tag}](https://github.com/{repo}/releases/tag/{tag})\n\n"
        f"Version: {archive['versionName']} ({archive['versionCode']})\n\n"
        f"Commit: [{sha[:8]}](https://github.com/{repo}/commit/{sha})\n\n"
        "This fixed release serves update.json and compatibility downloads. "
        "Each build has its own archive release; older compatibility assets remain available.\n",
        encoding='utf-8',
    )
    # Never send a publish/draft transition: this predates the repository immutability policy.
    gh('release', 'edit', 'nightly', '--target', sha, '--prerelease', '--latest=false',
       '--title', 'Nightly update channel', '--notes-file', str(notes), '--repo', repo)
    require_channel(repo)
    move_channel_ref(repo, sha)
    telegram_path = directory / 'telegram' / telegram_name
    telegram_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(files['apk'], telegram_path)
    return not unchanged, telegram_path


def main():
    repo, sha, tag = os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_SHA'], os.environ['NIGHTLY_TAG']
    directory = Path(tempfile.mkdtemp(prefix='nightly-channel-', dir=os.environ['RUNNER_TEMP']))
    advanced, apk_path = advance(repo, tag, sha, directory, os.environ['TELEGRAM_APK_NAME'])
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write(f'advanced={str(advanced).lower()}\n')
        if apk_path is not None:
            output.write(f'apk_path={apk_path}\n')


if __name__ == '__main__':
    main()
