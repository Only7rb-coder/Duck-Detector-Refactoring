/*
 * Copyright 2026 Duck Apps Contributor
 * If you have any questions, suggestions, or other inquiries, please email Eltavine <me@eltavine.com>.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "preload/early_detector.h"
#include "common/mount_id_observation.h"

#include <algorithm>
#include <cstddef>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

// The early detectors that read the topology of /proc/self/mountinfo: mount ID observations, tmpfs
// minor device numbering and peer group numbering.
namespace duckdetector::preload {

    namespace {

        struct MountInfoEntry {
            int id = 0;
            int parentId = 0;
            unsigned int major = 0;
            unsigned int minor = 0;
            std::string root;
            std::string target;
            std::string vfsOptions;
            int masterGroup = 0;
            std::string fsType;
            std::string source;
            std::string superOptions;
        };

        bool parse_mountinfo_line(const std::string &line, MountInfoEntry &entry) {
            std::istringstream iss(line);
            std::string devStr;

            if (!(iss >> entry.id >> entry.parentId >> devStr >> entry.root >> entry.target
                      >> entry.vfsOptions)) {
                return false;
            }

            const std::size_t colonPos = devStr.find(':');
            if (colonPos == std::string::npos) {
                return false;
            }
            entry.major = static_cast<unsigned int>(std::stoul(devStr.substr(0, colonPos)));
            entry.minor = static_cast<unsigned int>(std::stoul(devStr.substr(colonPos + 1)));

            entry.masterGroup = 0;
            std::string token;
            while (iss >> token && token != "-") {
                if (token.starts_with("shared:") || token.starts_with("master:")) {
                    const std::size_t fieldColon = token.find(':');
                    if (fieldColon != std::string::npos) {
                        entry.masterGroup = std::stoi(token.substr(fieldColon + 1));
                    }
                }
            }

            if (!(iss >> entry.fsType >> entry.source)) {
                return false;
            }
            std::getline(iss, entry.superOptions);
            return true;
        }

        std::vector<MountInfoEntry> parse_mountinfo() {
            std::vector<MountInfoEntry> entries;
            std::ifstream file("/proc/self/mountinfo");
            std::string line;
            while (std::getline(file, line)) {
                MountInfoEntry entry;
                if (parse_mountinfo_line(line, entry)) {
                    entries.push_back(entry);
                }
            }
            return entries;
        }

        bool is_path_or_child_of(const std::string &target, const std::string &prefix) {
            return target == prefix ||
                   (target.size() > prefix.size() &&
                    target.compare(0, prefix.size(), prefix) == 0 &&
                    target[prefix.size()] == '/');
        }

        bool is_platform_tmpfs_namespace_target(const std::string &target) {
            static const char *const kNamespaces[] = {
                    "/dev",
                    "/mnt",
                    "/storage",
                    "/apex",
                    "/linkerconfig",
                    "/sys",
                    "/tmp",
                    "/data_mirror",
                    "/debug_ramdisk",
            };

            for (const char *prefix: kNamespaces) {
                if (is_path_or_child_of(target, prefix)) {
                    return true;
                }
            }
            return false;
        }

        bool find_mount_minor_for_target(
                const std::vector<MountInfoEntry> &mounts,
                const std::string &target,
                unsigned int &outMinor
        ) {
            for (const auto &mount: mounts) {
                if (mount.target == target) {
                    outMinor = mount.minor;
                    return true;
                }
            }
            return false;
        }

    }  // namespace

    bool detect_mount_id_loophole(EarlyMountPreloadResult &result) {
        const auto observations = common::observe_mount_id_discontinuities(parse_mountinfo());
        result.mountIdGapDetected = !observations.empty();
        for (const auto &observation: observations) {
            result.findings.push_back("MOUNT_ID_GAP|" + observation.detail() + "|INFO");
        }
        return result.mountIdGapDetected;
    }

    bool detect_minor_dev_gap(EarlyMountPreloadResult &result) {
        const auto mounts = parse_mountinfo();
        unsigned int devMinor = 0;
        unsigned int sysMinor = 0;
        if (!find_mount_minor_for_target(mounts, "/dev", devMinor) ||
            !find_mount_minor_for_target(mounts, "/sys", sysMinor) ||
            sysMinor <= devMinor) {
            return false;
        }

        std::vector<MountInfoEntry> tmpfsMounts;
        for (const auto &mount: mounts) {
            if (mount.major == 0 && mount.root == "/" && mount.fsType == "tmpfs") {
                tmpfsMounts.push_back(mount);
            }
        }

        std::sort(
                tmpfsMounts.begin(),
                tmpfsMounts.end(),
                [](const MountInfoEntry &left, const MountInfoEntry &right) {
                    return left.minor < right.minor;
                }
        );

        unsigned int lastMinor = devMinor;
        for (const auto &mount: tmpfsMounts) {
            if (mount.minor <= devMinor) {
                continue;
            }
            if (mount.minor >= sysMinor) {
                break;
            }

            if (is_platform_tmpfs_namespace_target(mount.target)) {
                lastMinor = mount.minor;
                continue;
            }

            result.minorDevGapDetected = true;
            if (mount.minor != lastMinor + 1) {
                result.findings.push_back(
                        "MINOR_DEV_GAP|Expected minor " + std::to_string(lastMinor + 1) +
                        ", got " + std::to_string(mount.minor) + " at " + mount.target + "|WARNING"
                );
            } else {
                result.findings.push_back(
                        "MINOR_DEV_GAP|Unexpected tmpfs target before /sys: " + mount.target +
                        " (minor " + std::to_string(mount.minor) + ")|WARNING"
                );
            }
            return true;
        }

        return false;
    }

    bool detect_peer_group_gap(EarlyMountPreloadResult &result) {
        const auto mounts = parse_mountinfo();
        std::vector<bool> peerGroups;
        for (const auto &mount: mounts) {
            if (mount.masterGroup > 0) {
                if (peerGroups.size() < static_cast<std::size_t>(mount.masterGroup)) {
                    peerGroups.resize(static_cast<std::size_t>(mount.masterGroup), false);
                }
                peerGroups[static_cast<std::size_t>(mount.masterGroup - 1)] = true;
            }
        }

        constexpr int kAllowedGaps = 3;
        int gapCount = 0;
        for (bool present: peerGroups) {
            if (!present) {
                ++gapCount;
            }
        }

        if (gapCount > kAllowedGaps) {
            result.peerGroupGapDetected = true;
            result.findings.push_back(
                    "PEER_GROUP_GAP|Found " + std::to_string(gapCount) + " gaps (allowed " +
                    std::to_string(kAllowedGaps) + ")|WARNING"
            );
            return true;
        }

        return false;
    }

}  // namespace duckdetector::preload
