# Release policy

Duck Detector publishes two channels, both built from `main` and signed with the same key:

| Channel | Workflow | Version name | Manifest the app reads |
| --- | --- | --- | --- |
| Stable | `release-latest.yml`, on a stable tag | `26.10.0` | `releases/latest/download/update.json` |
| Nightly | `build.yml`, on every push to `main` | `26.10.0-nightly.5+1a2b3c4d` | `releases/download/nightly/update.json` |

A Nightly version names the last stable release behind it and the number of first-parent commits
since; before the first stable tag, Nightly builds keep their dated `2026.09.30-<hash>` name. Both
channels set versionCode to 300 plus the commits behind the build, so a Stable and a Nightly build
of the same commit are the same version and either channel can update to the other. An app that
moves from Nightly to Stable is usually ahead of the latest Stable; Android does not install an
older versionCode over a newer one, so the app waits for the first Stable newer than itself instead
of offering a downgrade.

The app follows the channel its build was published on until the user picks one under Settings →
About → Update channel.

## Cutting a stable release

Stable tags are calendar versions: a two-digit year, the month without padding, and a patch number
that starts at 0 each month, as in `v26.10.0`, `v26.10.1` and `v26.11.0`.

1. Wait until `main` holds everything the release should contain and its CI is green.
2. Tag the head of `main` and push the tag:

   ```bash
   git fetch origin
   git tag -s v26.10.0 -m "Duck Detector 26.10.0" origin/main
   git push origin v26.10.0
   ```

3. The tag starts `release-latest.yml`, which:
   - builds the APK and SDK AAR with `-Pduckdetector.releaseTag`, which names the version after the
     tag and fails the build unless the tag points at the commit being built;
   - asks GitHub to generate the release notes from the previous stable tag, or from the start of the
     history for the first stable release, and uses them as the changelog;
   - writes `update.json`, which carries that changelog for the app, and `SHA256SUMS`, and has GitHub
     attest the build provenance of the APK and AAR;
   - creates a draft release with every asset, checks each asset's name, size and digest, and only
     then publishes the release as the latest one;
   - waits until `releases/latest/download/update.json` serves the new manifest, and posts the APK to
     the Telegram channel.

GitHub's release notes list merged pull requests. Commits pushed to `main` without a pull request do
not appear in them, so add anything important to the notes on GitHub after publishing. The app shows
the notes captured in `update.json` when the release was published.

A published stable release is final: its files are what users and `update.json` already refer to,
and the workflow refuses to rebuild it. Tag a new patch version to fix a release. A draft left behind
by a failed run is deleted and rebuilt when the workflow runs again for the same tag.

Manual releases must start the workflow from the repository's default branch and name an existing
stable tag; the workflow never creates a tag from a branch head.

## Nightly releases

Every push to `main` replaces the assets of the `nightly` prerelease. Its release note and the
`changes` in its `update.json` list the first-parent changes since the last stable tag, newest
first, so the app can tell what a newer Nightly adds to the build it runs without GitHub's compare
API, which gh-proxy.com cannot relay. `.github/scripts/release_changes.py` collects them and
`.github/scripts/release_manifest.py` writes and validates the manifest for both channels.

## Trusted source history

A stable release tag must point to a commit reachable from the repository's default branch. This
keeps tag-controlled Gradle and Android build logic inside the same reviewed history that is
allowed to receive the release signing credentials.

## Historical rebuild compatibility

Historical rebuilds are supported starting at commit
`641913a4efa743b200454b10b4d2e41ac02f1f39` (inclusive). That revision introduced the complete
`duckdetector.android.*` toolchain property contract consumed by the current shared Android setup
action. Tags on older commits are rejected rather than built with guessed modern SDK, build-tools,
CMake, or NDK versions. A stable tag must also be on a commit whose build logic reads
`duckdetector.releaseTag`; on older commits the build does not name the tag's version, and the
workflow stops before publishing.

The release workflow uses the workflow revision's shared setup action through the `$/` self-
repository reference, and the workflow revision's release scripts, while building source from the
selected tag. The selected source tree still supplies its own pinned toolchain values and Gradle
build logic.

## Release immutability

Enable the repository's **Release immutability** setting. All newly published releases,
including stable releases and Nightly archives, must report `immutable: true`. Both workflows
verify this state after publication. Stable publication does not overwrite existing assets.

The existing, already published `nightly` prerelease is the only mutable exception. GitHub's
setting applies to future publications, so this pre-existing release remains mutable. Never
unpublish, delete, recreate or republish it; recreating it under the enabled policy would lock
its update assets and tag. Channel updates modify assets and notes without a draft transition.
The Nightly workflow stops if this channel is missing, unpublished or immutable, rather than
attempting to recreate it. The stable workflow rejects the reserved literal `nightly` tag.

GitHub locks release assets and the associated tag; release titles, notes and latest/prerelease
metadata can still be edited. The repository currently has no older releases to migrate other
than the mutable `nightly` exception.

References: [GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
and [enabling the policy](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes).

## Nightly archives and the update channel

Each default-branch Nightly is archived under `nightly-yyyy-MM-dd-gitshortHash`, using the
build-start UTC date and the first eight hexadecimal characters of the full commit SHA.
The tag must resolve to that full commit. The APK, SDK AAR and schema-v1 `update.json` are
uploaded to a draft, verified, and only then published as a prerelease. Published archives
must be immutable and are never replaced; a rerun on the same date/commit reuses the existing complete archive.
Only unpublished draft attempts may be discarded and rebuilt. GitHub may assign draft assets
a temporary `untagged-*` URL; manifests always contain the final archive URL. The draft URL,
asset identity, size and digest are checked before publication, and permanent URLs afterwards.

The fixed `nightly` tag remains the compatibility and discovery channel:

- Checks still read `releases/download/nightly/update.json`; no release-list API is needed.
- The manifest's original `apk` object points to an APK under `nightly`, as existing clients
  require. Its schema version and legacy `master` branch value remain unchanged.
- An optional `archive` object identifies the immutable tag and the same APK bytes there.
  New clients require the names, sizes and hashes to agree, select the immutable URL, and
  independently verify against that tag's official manifest. Legacy clients ignore the addition.
- The channel is advanced only after its public archive and both compatibility assets have
  been verified. The manifest is replaced last. If this upload/verification fails, the previous
  manifest is restored when one exists. GitHub asset replacement is not a transaction; a brief
  missing-manifest response is possible and remains subject to the client's existing HTTP retries.
- Only the literal `nightly` ref may move. Its ref is updated after the pointer is verified.
  An older version/build time cannot move the channel backwards; interrupted ref updates can
  be repaired by rerunning the workflow without replacing the archive or the current pointer.
- Compatibility APK/AAR filenames include the immutable tag. Existing channel assets are
  reused only after byte verification, never clobbered or automatically deleted, so older
  downloads retain their URLs. The channel therefore also retains compatibility copies of
  historical binaries; archive retention and channel retention must be considered separately.

The Telegram step uses the verified archived APK bytes. It sends a notification only when
this run advances the update channel; a rerun of an already current channel does not resend it.
The publishing helpers' offline fixtures run in CI as `test-nightly-release.py` and
`test-nightly-channel.py`, including the workflow's actual manifest-building script.

API semantics: [GitHub releases](https://docs.github.com/en/rest/releases/releases) and
[release assets](https://docs.github.com/en/rest/releases/assets). The already pinned
[release action](https://github.com/softprops/action-gh-release) uploads the draft assets.
