# Dangerous Apps evidence record

Status: reviewed

New HMA behavior checks have the coverage and limitations described below.

The Dangerous Apps detector asks whether root managers, hooking frameworks and similar tools are installed, including when package visibility filtering or hiding modules keep them out of PackageManager.

## Signals

### PackageManager inventory

- Observable signal: installed packages from PackageManager, and targets PackageManager omits although other methods find them.
- Producing subsystem: PackageManager and its package visibility filtering.
- Mechanism: an installed target appears in PackageManager unless visibility filtering or a hiding module removes it.
- References: frameworks/base core/java/android/content/pm/PackageManager.java; capability/packageinventory/EVIDENCE.md.
- Applicability: full visibility needs QUERY_ALL_PACKAGES from the host.
- Visibility limits: restricted visibility is reported; hidden-from-PackageManager findings are only computed under full visibility.
- Result states: detected, hidden from PackageManager, restricted, clean.
- Interpretation: an installed target is a finding at its category's severity; hiding from PackageManager is stronger.

### Filesystem and provider methods

- Observable signal: package contexts, APK file descriptors, directory listings with zero-width and ignorable codepoints, FUSE stats, the native /data/data stat and tool-specific special paths.
- Producing subsystem: the filesystem through DAC, SELinux and MediaProvider's FUSE layer.
- Mechanism: each method reaches the package's files by a path PackageManager filtering does not control.
- References: capability/packageinventory/EVIDENCE.md (data directory stat); kernel/common Documentation/filesystems/proc.rst for fd views. Discovery only for the Unicode path bypass methods.
- Applicability: every release; FUSE behaviour depends on the MediaProvider version.
- Visibility limits: the native stat can fail to run, which the report records as an issue instead of no hits.
- Result states: detected by method, method not run, clean.
- Interpretation: each method adds evidence for the same package; findings list every method that saw it.

### Tool-specific channels

- Observable signal: the Scene loopback and broadcast channels, Thanox IPC, and enabled accessibility services.
- Producing subsystem: the tools' own services.
- Mechanism: these tools answer on fixed local channels.
- References: Discovery only: the channels follow each tool's observed behaviour.
- Applicability: every release.
- Visibility limits: a tool that is installed but not running does not answer.
- Result states: detected, clean.
- Interpretation: an answering tool is a finding for that tool.


## HMA behavior checks

### Provenance

The detection-vector report for these checks comes from [frknkrc44](https://github.com/frknkrc44),
maintainer of [HMA-OSS](https://github.com/frknkrc44/HMA-OSS), as supplied with the feature request.
Credit for that report belongs to frknkrc44, not Chunqiu or Wuying. This attribution describes the
source used here; it makes no claim about independent prior discoveries. The seccomp and kernel
countermeasures mentioned in the report are not incorporated. No third-party code or text is copied
or adapted; the implementation uses the documented mechanisms and independent tests below.

### Permission-exception consistency

- Observable signal: repeated target-package calls return normally or throw NameNotFoundException,
  while caller-package controls before and after both throw SecurityException.
- Producing subsystem: ActivityManager's incoming-user permission checks and PackageManager's
  MATCH_ANY_USER flag permission enforcement, including the public Java wrapper's null conversion.
- Mechanism: sendBroadcastAsUser targets another user with an explicit, randomly named absent
  receiver. Package/application-info calls use AOSP's hidden MATCH_ANY_USER flag (0x00400000) for
  the current user; that flag requires cross-user permission even for the caller's user. No receiver
  is registered, no target component is launched, and no global hidden-API exemption is installed.
- Primary references: Android [Context](https://developer.android.com/reference/android/content/Context)
  and [PackageManager](https://developer.android.com/reference/android/content/pm/PackageManager);
  AOSP Android 10 [PackageManagerService.updateFlagsForPackage](https://android.googlesource.com/platform/frameworks/base/+/android-10.0.0_r1/services/core/java/com/android/server/pm/PackageManagerService.java),
  Android 14 [ComputerEngine](https://android.googlesource.com/platform/frameworks/base/+/android-14.0.0_r1/services/core/java/com/android/server/pm/ComputerEngine.java),
  [ApplicationPackageManager](https://android.googlesource.com/platform/frameworks/base/+/android-14.0.0_r1/core/java/android/app/ApplicationPackageManager.java),
  [ContextImpl](https://android.googlesource.com/platform/frameworks/base/+/android-14.0.0_r1/core/java/android/app/ContextImpl.java),
  [UserController.handleIncomingUser](https://android.googlesource.com/platform/frameworks/base/+/android-14.0.0_r1/services/core/java/com/android/server/am/UserController.java),
  and Android 16 [ComputerEngine](https://android.googlesource.com/platform/frameworks/base/+/android-16.0.0_r1/services/core/java/com/android/server/pm/ComputerEngine.java)
  / [BroadcastController](https://android.googlesource.com/platform/frameworks/base/+/android-16.0.0_r1/services/core/java/com/android/server/am/BroadcastController.java).
- Upstream implementation references: HMA-OSS [oss-164 BroadcastHook](https://github.com/frknkrc44/HMA-OSS/blob/oss-164/zygote/src/main/java/org/frknkrc44/hma_oss/zygote/hook/BroadcastHook.kt)
  returns before the framework permission path; [oss-173 PmsHookTargetBase](https://github.com/frknkrc44/HMA-OSS/blob/oss-173/zygote/src/main/java/org/frknkrc44/hma_oss/zygote/hook/PmsHookTargetBase.kt)
  preserves existing exceptions. These are version/configuration fingerprints, not generic Zygisk tests.
- Applicability: Android 10–16 (API 29–36); later releases explicitly return unsupported pending
  source verification. Privileged callers, OEM changes or failed hidden-method lookup invalidate
  controls and yield inconclusive results.
- Visibility limits: at most 128 distinct catalog targets absent from the current inventory are
  compared, prioritizing packages already corroborated by other probes before catalog order; the report retains the selected/total scope. AOSP package filtering
  alone does not establish a mismatch because permission checks precede target lookup.
- Result states: consistent in evaluated scope, inconsistent, inconclusive, partial, unsupported.
- Interpretation: only repeated differences with passing controls produce warning-level evidence.
  NameNotFoundException alone is normal package-query behavior, not evidence of hiding.

### Ordered-broadcast final callback

- Observable signal: whether the final callback arrives with ordered=false, sticky=false and the
  initial result code, data and token extras preserved, with no matching target receiver.
- Producing subsystem: framework broadcast queues and final IIntentReceiver delivery.
- Mechanism: sendOrderedBroadcast uses the same absent explicit component. Caller-package controls
  bracket repeated target calls. Each callback has a 1 s timeout; the target batch has a 6 s budget.
  Late callbacks are ignored after coroutine cancellation; no receiver registration is retained.
  These budgets bound asynchronous callback waits; a blocked synchronous Binder submission cannot
  be preempted by coroutine cancellation and may delay the scan.
- Primary references: AOSP Android 10 [BroadcastQueue](https://android.googlesource.com/platform/frameworks/base/+/android-10.0.0_r1/services/core/java/com/android/server/am/BroadcastQueue.java)
  sends the final result with false/false; Android 14 [BroadcastQueueModernImpl.scheduleResultTo](https://android.googlesource.com/platform/frameworks/base/+/android-14.0.0_r1/services/core/java/com/android/server/am/BroadcastQueueModernImpl.java)
  schedules the final receiver through IApplicationThread with ordered=false.
- Upstream reference: HMA-OSS [oss-173 BroadcastHook](https://github.com/frknkrc44/HMA-OSS/blob/oss-173/zygote/src/main/java/org/frknkrc44/hma_oss/zygote/hook/BroadcastHook.kt)
  invokes performReceive directly with the BroadcastRecord.ordered flag.
- Applicability: API 29–36, at most 24 selected targets, prioritizing already corroborated packages. The report does not
  assert the behavior of an unspecified newer HMA test build.
- Visibility limits: load, scheduling, process freezing and OEM behavior can delay callbacks. Missing
  callbacks, failed controls, or a one-off difference remain inconclusive. Repeated payload/flag
  differences are warning-level evidence of callback-semantic inconsistency, without module identity.
- Result states: consistent, inconsistent, inconclusive, partially evaluated, unsupported.

### UID task visibility through getpriority

- Observable signal: getpriority(PRIO_USER, uid) succeeds for an ordinary app UID while repeated
  PackageManager.getPackagesForUid queries return no package association.
- Producing subsystem: Android Common Kernel task/credential traversal, with the process's user and
  PID namespaces, independent of PackageManager's package filtering.
- Mechanism: syscall(SYS_getpriority) bypasses the getpriority libc wrapper. A disposable child checks
  its own UID, then queries this Android user's app IDs 10000–19999, excluding SDK-sandbox and isolated
  UID ranges. The child has a 500 ms scan budget, a 512-hit bound and the common runner's 1 s run /
  250 ms reap deadlines. Up to 16 unassociated UID candidates are queried again in children, bracketed
  by successful caller-package association controls. The raw kernel 1..40 return is converted to
  nice=20-return; failures, signals and seccomp refusal never become absence.
- Primary references: ACK android14-6.1 at `48e3cd973485465613da6d71fa17139773546573`, [kernel/sys.c:getpriority](https://android.googlesource.com/kernel/common/+/48e3cd973485465613da6d71fa17139773546573/kernel/sys.c)
  (`task_uid`, `task_pid_vnr`, initial ESRCH); AOSP Android 14
  [bionic getpriority.cpp](https://android.googlesource.com/platform/bionic/+/android-14.0.0_r1/libc/bionic/getpriority.cpp)
  and [UserHandle](https://android.googlesource.com/platform/frameworks/base/+/android-14.0.0_r1/core/java/android/os/UserHandle.java);
  [package visibility](https://developer.android.com/training/package-visibility);
  [JNI resolving native method names](https://docs.oracle.com/en/java/javase/17/docs/specs/jni/design.html#resolving-native-method-names).
- Applicability: built for arm64-v8a, armeabi-v7a, x86 and x86_64; full package visibility and an ordinary
  application UID are required. ACK 6.1 is a semantics reference, not a guarantee about OEM patches.
- Visibility limits: non-running apps have no task signal; package associations can change through
  uninstall/update, shared UIDs and OEM policy. PackageManager may also be hooked. A UID is not a
  package identity. A completed scan only covers the stated user/range at sampling time. Seccomp,
  namespaces and kernel filtering can hide tasks; no absent task means no installed package.
- Result states: consistent, inconsistent, inconclusive, partial, permission limited, unavailable,
  unsupported. UID-only mismatches are warnings and never add a named hidden package or red HMA alert. A completed
  scan that omits the live caller UID invalidates the comparison.

### Validation coverage

Unit tests cover repeated permission controls, public-wrapper NameNotFoundException conversion,
failed controls, ordered/sticky/payload callback differences, timeout uncertainty, native payload
framing, raw priority conversion, partial scans and warning-level presentation. Native builds cover
all four supported ABIs. Device results are recorded after executing the runtime checks; no HMA
version or configuration is assumed from package presence alone.

The monitored catalog includes `org.frknkrc44.hma_oss`, as declared by the upstream HMA-OSS
application and observed in the device package inventory. Target prioritization affects sampling
coverage only; it does not promote the corroborating probes into stronger evidence.


#### Release coverage of the framework contracts

The MATCH_ANY_USER permission requirement and final-callback ordered=false rule were also checked
in the intervening release sources:

| Android tag | Permission enforcement | Final callback |
| --- | --- | --- |
| android-11.0.0_r1 | [PackageManagerService](https://android.googlesource.com/platform/frameworks/base/+/android-11.0.0_r1/services/core/java/com/android/server/pm/PackageManagerService.java) | [BroadcastQueue](https://android.googlesource.com/platform/frameworks/base/+/android-11.0.0_r1/services/core/java/com/android/server/am/BroadcastQueue.java) |
| android-12.0.0_r1 | [PackageManagerService](https://android.googlesource.com/platform/frameworks/base/+/android-12.0.0_r1/services/core/java/com/android/server/pm/PackageManagerService.java) | [BroadcastQueue](https://android.googlesource.com/platform/frameworks/base/+/android-12.0.0_r1/services/core/java/com/android/server/am/BroadcastQueue.java) |
| android-13.0.0_r1 | [ComputerEngine](https://android.googlesource.com/platform/frameworks/base/+/android-13.0.0_r1/services/core/java/com/android/server/pm/ComputerEngine.java) | [BroadcastQueue](https://android.googlesource.com/platform/frameworks/base/+/android-13.0.0_r1/services/core/java/com/android/server/am/BroadcastQueue.java) |
| android-15.0.0_r1 | [ComputerEngine](https://android.googlesource.com/platform/frameworks/base/+/android-15.0.0_r1/services/core/java/com/android/server/pm/ComputerEngine.java) | [BroadcastQueueModernImpl](https://android.googlesource.com/platform/frameworks/base/+/android-15.0.0_r1/services/core/java/com/android/server/am/BroadcastQueueModernImpl.java) |
| android-16.0.0_r1 | ComputerEngine linked above | [BroadcastQueueImpl](https://android.googlesource.com/platform/frameworks/base/+/android-16.0.0_r1/services/core/java/com/android/server/am/BroadcastQueueImpl.java) |

#### Device observations (2026-10-18, Asia/Singapore)

PJX110, Android 16/API 36, security patch 2026-04-01, arm64, kernel
`6.1.141-android14-11-o-gd2a6093d5589`. Package metadata reports HMA-OSS `oss-162`
(versionCode 7304645); Duck Detector's blacklist scope is user-reported. Module configuration was
not changed. Two consecutive scanner/export runs completed successfully through the generic
DetectorRuntimeSmokeTest, with caller-package controls passing in each run:

| Check | Observed result in each run |
| --- | --- |
| Broadcast cross-user exception | 124/124 catalog targets repeated; no exception mismatch |
| getPackageInfo exception | 124/124 targets repeated; nine target package names had repeated exception mismatches |
| getApplicationInfo exception | 124/124 targets repeated; the same nine target names had repeated exception mismatches |
| Ordered-broadcast final result | 24/24 selected targets repeated; flags and payloads matched; overall catalog coverage remains partial (24/124) |
| getpriority UID visibility | 10000/10000 UIDs checked; 87 then 86 task UIDs; the same eight UIDs repeatedly lacked visible package associations; no recheck failure or recheck-limit truncation |

The nine queried names are behavior fingerprints, not proof that nine corresponding apps are
installed. The eight UIDs do not identify hidden packages. These observations do not validate
HMA-OSS 173's manual-resultTo positive case, a newer fixed test release, or an HMA-disabled/stock
baseline. No enforced seccomp-denial profile was installed. Older Android releases and other ABIs have source/build coverage, not device coverage.

Reproduce the runtime smoke check after building and installing the debug and androidTest APKs:

```sh
./gradlew :app:assembleDebug :app:assembleDebugAndroidTest
adb shell am instrument -w -r \
  -e class com.eltavine.duckdetector.DetectorRuntimeSmokeTest \
  -e detectorId dangerous_apps \
  com.eltavine.duckdetector.test/androidx.test.runner.AndroidJUnitRunner
adb logcat -d -s DetectorRuntimeSmoke:I
```

The context bootstrap instrumented test and MainActivity cold-launch smoke check also passed.
The runtime harness uses AndroidX Test Runner 1.7.0 with the existing JUnit extension 1.3.0;
[runner documentation](https://developer.android.com/training/testing/instrumented-tests/androidx-test-libraries/runner)
and [release notes](https://developer.android.com/jetpack/androidx/releases/test) establish the
runtime dependency. Twenty-five new JVM tests cover the control, failure and reduction rules.


The following validation commands passed with the repository-pinned uihelper revision
`18bed84a78dd811889ea1813d6954a988e44d688`:

```sh
./gradlew :app:assembleDebug :app:assembleDebugAndroidTest unitTest :app:lintDebug buildHealth :sdk:aar:publish :sdk:aar:verifySdkAar
./gradlew :build-logic:test
./gradlew -p samples/sdk-consumer assembleDebug
for s in .github/scripts/check-*.py; do python3 "$s" || exit 1; done
```

The SDK consumer is a separate build using only the published AAR and Maven dependencies. Both
Debug and Release native library builds completed for arm64-v8a, armeabi-v7a, x86 and x86_64.
