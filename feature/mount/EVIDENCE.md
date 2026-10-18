# Mount evidence record

Status: reviewed

The Mount detector asks whether this app's mount view shows systemless root mounts, writable or overlaid system partitions, or hiding that leaves inconsistencies between kernel views. Findings describe this process's mount namespace.

## Signals

### Mount tables

- Observable signal: /proc/self/mounts and /proc/self/mountinfo entries for Magisk paths, writable system partitions, overlays, loop devices and dm-verity bypass patterns.
- Producing subsystem: the kernel's mount tables for this process's namespace.
- Mechanism: systemless root bind-mounts or overlays module files over system paths, which the mount tables list.
- References: kernel/common Documentation/filesystems/proc.rst section 3.5 (mountinfo fields, mount IDs, propagation).
- Applicability: every release; hiding modules unmount their entries from the app's namespace.
- Visibility limits: a namespace that hides the mounts leaves only indirect inconsistencies.
- Result states: hits, clean, unavailable.
- Interpretation: root mounts and writable system partitions are danger.

### Mount ID discontinuities

- Observable signal: nonconsecutive IDs in adjacent mountinfo records immediately after `/apex/com.android.art` or before `/data_mirror`, including descending IDs. Retain both paths and IDs; do not count absent mounts from adjacent rows.
- Producing subsystem: the kernel mount allocator and this process's mount namespace.
- Mechanism: the allocator is global and releases IDs for reuse, while procfs iterates the selected namespace's mount list. Neither allocation nor record adjacency establishes a consecutive sequence for one process. A number between adjacent rows can also occur elsewhere in the same file.
- References: primary sources pinned to inspected revisions:
  - [ACK android14-5.15 `fs/namespace.c`](https://android.googlesource.com/kernel/common/+/9debaec240781879fbb65b9303f7cfc3e1d96c02/fs/namespace.c): `mnt_id_ida`, `mnt_alloc_id`, `mnt_free_id`.
  - [ACK android14-6.1 `fs/namespace.c`](https://android.googlesource.com/kernel/common/+/48e3cd973485465613da6d71fa17139773546573/fs/namespace.c): the same allocator, plus `mnt_list_next`, `m_start`, `m_next`, and `copy_mnt_ns`.
  - [ACK android15-6.6 `fs/namespace.c`](https://android.googlesource.com/kernel/common/+/150617c4d667a241e52441e855befac0c5d6702b/fs/namespace.c): the same allocation/release semantics.
  - [ACK 6.1 procfs documentation, section 3.5](https://android.googlesource.com/kernel/common/+/48e3cd973485465613da6d71fa17139773546573/Documentation/filesystems/proc.rst): IDs identify mounts and may be reused after unmount.
  - [ACK 6.1 `fs/proc_namespace.c`](https://android.googlesource.com/kernel/common/+/48e3cd973485465613da6d71fa17139773546573/fs/proc_namespace.c): `mounts_open_common` selects the task namespace and root; `show_mountinfo` emits mount IDs.
  - [Android application sandbox](https://source.android.com/docs/security/app-sandbox) and [Android 14 Zygote](https://android.googlesource.com/platform/frameworks/base/+/299fe6f5d6fc6f1af7c3411dcf4e5efdf7217368/core/jni/com_android_internal_os_Zygote.cpp): `isolateAppData` mounts tmpfs over app data parents and binds app directories from `/data_mirror`; this is legitimate platform behavior.
- Applicability: the inspected ACK branches and Android 14 source establish counterexamples to a universal continuity assumption. They do not identify an OEM device's exact sequence of allocations, releases, namespace cloning, or mount changes. These sources provide no consecutive-ID security contract or justified gap threshold.
- Result states: informational observation or no local observation. Runtime separately reports mountinfo availability. The preload has no per-probe read/parse availability field, so an absent observation there does not establish successful collection. The shared routine only consumes parsed entries.
- Interpretation: INFO only, regardless of interval size or repeated startup/runtime captures. These captures are not independent corroboration. Historical field and finding IDs containing `loophole` are retained for payload compatibility; the displayed label is `Mount ID discontinuity`. Legacy DANGER records are also interpreted as INFO.
- Visibility limits: discontinuities cannot establish root, hiding, or a clean device. A contiguous list does not exclude hidden mounts. The origin of specific IDs requires a contemporaneous complete namespace snapshot and device/build/kernel context; a screenshot is insufficient.
- Validation: `python3 core/native/src/test/python/test_mount_id_observation.py` checks the shared native policy, including 1/2/3/8/16 intervals, IDs visible elsewhere, descending/reused IDs, integer boundaries, and absent anchors. Mount repository, preload result, and card mapper unit tests verify that observations and denied coverage do not raise risk, while independent direct artifacts and statx contradiction classifications remain intact. These are synthetic regressions, not captured stock/OEM device validation.

### Cross-view consistency

- Observable signal: peer group gaps, minor device gaps, statx mount IDs and mount-root attributes that disagree with mountinfo, and namespace identity versus init.
- Producing subsystem: the kernel's mount ID allocator, statx and namespace links.
- Mechanism: statx and mountinfo can expose differing mount identities; allocator gaps alone do not establish such a contradiction. Peer/minor group heuristics are separate signals and are not strengthened by mount ID discontinuities.
- References: kernel/common Documentation/filesystems/proc.rst (mountinfo mount IDs and propagation); kernel/common include/uapi/linux/stat.h (STATX_MNT_ID and STATX_ATTR_MOUNT_ROOT).
- Applicability: statx needs a kernel that fills the mount ID fields; /proc/1/ns/mnt is readable only where SELinux allows it.
- Visibility limits: a different app/init mount namespace is compatible with ordinary Zygote isolation. Namespace identity or access denial alone does not establish root.
- Result states: anomaly, mount root, review, clean, unsupported, unavailable.
- Interpretation: contradictions between kernel views are danger; a mount-root attribute on a system subdirectory is a warning.

### Maps, Zygote Next and isolated process mount views

- Observable signal: Zygisk, Riru and Magisk-hidden library paths in /proc/self/maps, the Zygote Next mount view compared with the main process, and the mount views of other processes collected by the isolated helper.
- Producing subsystem: this process's address space, zygote's mount namespace setup, and the helper process.
- Mechanism: a root solution that isolates mounts per app leaves the app's view different from zygote's and from other processes'.
- References: kernel/common Documentation/filesystems/proc.rst (maps, mountinfo and ns); frameworks/base core/jni/com_android_internal_os_Zygote.cpp; capability/helperprocess/EVIDENCE.md.
- Applicability: every release; the isolated helper must start.
- Visibility limits: other processes' mount views are visible only where procfs allows.
- Result states: hits, drift, clean, unavailable.
- Interpretation: injected library paths and namespace drift are danger or warning by finding.

### Paths, startup preload and shell tmp

- Observable signal: busybox and hybrid mount paths, the mount view captured by the NativeActivity before the activity it launches, and whether /data/local/tmp is hidden or remapped.
- Producing subsystem: the filesystem and the early launch capture.
- Mechanism: hiding applied after launch leaves differences between the early and later views.
- References: capability/earlypreload/EVIDENCE.md for the capture; system/core rootdir/init.rc for /data/local/tmp. Discovery only for busybox and hybrid mount paths.
- Applicability: the preload needs the transparent launcher; SDK hosts without it report it unavailable.
- Visibility limits: denied path checks and inaccessible init namespace links describe partial coverage. They are informational regardless of their count and do not corroborate root or mount hiding.
- Result states: hits, signals, busybox, clean, partial, unavailable.
- Interpretation: path and preload hits follow their finding severity; busybox alone is a warning.
