#!/usr/bin/env python3
# Copyright 2026 Duck Apps Contributor
# If you have any questions, suggestions, or other inquiries, please email Eltavine <me@eltavine.com>.
# SPDX-License-Identifier: Apache-2.0
"""Prepare a unique Nightly draft or publish its verified, complete asset set."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


def gh(*args, missing_ok=False):
    result = subprocess.run(['gh', *args], capture_output=True, text=True)
    if result.returncode:
        if missing_ok and '(HTTP 404)' in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip() or 'GitHub request failed')
    return json.loads(result.stdout) if args[0] == 'api' and result.stdout.strip() else None


def find_release(repo, tag):
    release = gh('api', f'repos/{repo}/releases/tags/{tag}', missing_ok=True)
    if release is not None:
        return release
    # The by-tag endpoint omits drafts. Draft retries must locate the existing release.
    pages = gh('api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100')
    matches = [item for page in pages for item in page if item.get('tag_name') == tag]
    if len(matches) > 1:
        raise ValueError('Multiple releases use the same archive tag')
    return matches[0] if matches else None


def require_channel(repo):
    release = gh('api', f'repos/{repo}/releases/tags/nightly', missing_ok=True)
    if (release is None or release.get('tag_name') != 'nightly' or release.get('draft') is not False
            or release.get('immutable') is not False or release.get('prerelease') is not True):
        raise ValueError('An existing public, mutable nightly prerelease is required; never recreate or republish it')
    return release


def require_immutable(release):
    if release.get('draft') is not False or release.get('immutable') is not True:
        raise ValueError('The published archive must be immutable; enable repository release immutability')


def canonical_asset_url(repo, tag, name, draft_url):
    """Draft assets may use GitHub's temporary untagged URL until publication."""
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9._+-]+', name):
        raise ValueError('Unsafe archive asset name')
    url = urlsplit(draft_url)
    prefix = f'/{repo}/releases/download/'
    remainder = url.path.removeprefix(prefix)
    source_tag, separator, source_name = remainder.partition('/')
    if (url.scheme != 'https' or url.netloc != 'github.com' or url.query or url.fragment
            or not url.path.startswith(prefix) or separator != '/' or source_name != name
            or (source_tag != tag and not re.fullmatch(r'untagged-[0-9a-z]+', source_tag))):
        raise ValueError('Draft asset URL is outside the expected GitHub release')
    return f'https://github.com/{repo}/releases/download/{tag}/{name}'


def require_public_urls(release, repo, tag):
    for asset in release.get('assets', []):
        expected = f'https://github.com/{repo}/releases/download/{tag}/{asset["name"]}'
        if asset.get('browser_download_url') != expected:
            raise ValueError('Published archive asset URL does not match its permanent tag')


def nightly_tag(timestamp, sha):
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('A complete lowercase commit SHA is required')
    instant = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    if instant.utcoffset() is None:
        raise ValueError('The build timestamp must include its timezone')
    return f"nightly-{instant.astimezone(timezone.utc):%Y-%m-%d}-{sha[:8]}"


def validate_tag(tag, sha):
    match = re.fullmatch(r'nightly-(\d{4}-\d{2}-\d{2})-([0-9a-f]{8})', tag)
    if not match or match[2] != sha[:8] or not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Nightly tag does not identify the expected commit')
    datetime.strptime(match[1], '%Y-%m-%d')


def verify_ref(repo, tag, sha):
    ref = gh('api', f'repos/{repo}/git/ref/tags/{tag}', missing_ok=True)
    if ref is None:
        gh('api', '-X', 'POST', f'repos/{repo}/git/refs', '-f', f'ref=refs/tags/{tag}', '-f', f'sha={sha}')
        return
    obj = ref['object']
    for _ in range(4):
        if obj['type'] == 'commit':
            if obj['sha'] != sha:
                raise ValueError('Existing Nightly tag points at another commit')
            return
        if obj['type'] != 'tag':
            break
        obj = gh('api', f'repos/{repo}/git/tags/{obj["sha"]}')['object']
    raise ValueError('Nightly tag does not resolve to a commit')


def require_complete(release, tag):
    if release.get('tag_name') != tag or release.get('prerelease') is not True:
        raise ValueError('Unexpected Nightly release identity or type')
    assets = release.get('assets', [])
    names = [item.get('name', '') for item in assets]
    if (len(assets) != 3 or names.count('update.json') != 1
            or sum(name.endswith('.apk') for name in names) != 1
            or sum(name.endswith('.aar') for name in names) != 1
            or any(item.get('state') != 'uploaded' or item.get('size', 0) <= 0 for item in assets)):
        raise ValueError('Nightly must contain exactly one complete APK, AAR and update.json')


def prepare(repo, sha, timestamp):
    tag = nightly_tag(timestamp, sha)
    require_channel(repo)
    verify_ref(repo, tag, sha)
    release = find_release(repo, tag)
    if release is not None:
        if release.get('draft') is False:
            require_complete(release, tag)
            require_immutable(release)
            require_public_urls(release, repo, tag)
            return tag, False
        if release.get('draft') is not True:
            raise ValueError('Release did not report its draft state')
        # Only unpublished attempts may be rebuilt. Never delete the tag or a public release.
        gh('release', 'delete', tag, '--yes', '--repo', repo)
    return tag, True


def publish(repo, sha, tag):
    validate_tag(tag, sha)
    verify_ref(repo, tag, sha)
    release = find_release(repo, tag)
    if release is None:
        raise ValueError('The archive draft is missing')
    require_complete(release, tag)
    if release.get('draft') is not True:
        raise ValueError('Only a verified draft may be published')
    gh('release', 'edit', tag, '--draft=false', '--prerelease', '--latest=false', '--repo', repo)
    published = gh('api', f'repos/{repo}/releases/tags/{tag}')
    require_complete(published, tag)
    require_immutable(published)
    require_public_urls(published, repo, tag)
    verify_ref(repo, tag, sha)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'publish'])
    operation = parser.parse_args().operation
    repo, sha = os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_SHA']
    if operation == 'publish':
        publish(repo, sha, os.environ['NIGHTLY_TAG'])
    else:
        tag, should_publish = prepare(repo, sha, os.environ['BUILD_TIME_ISO'])
        with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
            output.write(f'tag={tag}\npublish={str(should_publish).lower()}\n')


if __name__ == '__main__':
    main()
