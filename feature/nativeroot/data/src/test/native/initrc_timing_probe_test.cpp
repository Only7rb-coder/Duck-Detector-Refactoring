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

#include "initrc_timing_probe.h"

#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <fcntl.h>
#include <limits.h>
#include <map>
#include <pthread.h>
#include <string>
#include <unistd.h>
#include <vector>

using namespace duckdetector::nativeroot;
namespace {
enum class Fault { NONE, NAME, READBACK, PIN, CPU, CPU_DENIED, CLOCK, STAT, ATTRIBUTES, BUDGET, CLEANUP, DOUBLE_FAILURE };
struct Fixture {
    Fault fault = Fault::NONE;
    pthread_t caller = pthread_self();
    std::string name = "original";
    std::int64_t clock = 1000;
    int calls = 0;
    int control_fd = -1;
    int name_changes = 0;
    bool worker_only = true;
    std::vector<int> conditions;
    std::string leftover;
};
Fixture *fixture;
std::string fd_path(int fd) {
    char path[PATH_MAX]{};
#if defined(__APPLE__)
    assert(fcntl(fd, F_GETPATH, path) == 0);
#else
    char link[64];
    std::snprintf(link, sizeof(link), "/proc/self/fd/%d", fd);
    assert(readlink(link, path, sizeof(path) - 1) > 0);
#endif
    return path;
}
int set_name(const char *name) {
    fixture->worker_only &= !pthread_equal(fixture->caller, pthread_self());
    ++fixture->name_changes;
    if (fixture->fault == Fault::NAME && fixture->name_changes == 2) { errno = EPERM; return -1; }
    fixture->name = name;
    return 0;
}
int get_name(char *name) {
    const auto &value = fixture->fault == Fault::READBACK && fixture->name_changes > 0
        ? std::string("wrong") : fixture->name;
    std::strcpy(name, value.c_str());
    return 0;
}
int pin_cpu() {
    fixture->worker_only &= !pthread_equal(fixture->caller, pthread_self());
    if (fixture->fault == Fault::PIN) { errno = EACCES; return -1; }
    return 2;
}
int cpu() {
    if (fixture->fault == Fault::CPU_DENIED) { errno = EPERM; return -1; }
    return fixture->fault == Fault::CPU && fixture->calls ? 3 : 2;
}
std::int64_t now() {
    if (fixture->fault == Fault::CLOCK) { errno = EIO; return 0; }
    return fixture->clock;
}
int stat_fd(int fd, struct stat *attributes) {
    assert(!pthread_equal(fixture->caller, pthread_self()));
    const auto path = fd_path(fd);
    // Darwin F_GETPATH can return either hard-link alias for one vnode. Warm-up starts with
    // the control descriptor; retain fd identity instead of treating that path as authoritative.
    if (fixture->control_fd < 0) fixture->control_fd = fd;
    const bool target = fd != fixture->control_fd;
    const int condition = (fixture->name == "init" ? 2 : 0) + (target ? 1 : 0);
    if (fixture->calls % kInitRcIterations == 0) fixture->conditions.push_back(condition);
    ++fixture->calls;
    if (fixture->fault == Fault::STAT) { errno = EACCES; return -1; }
    const int result = fstat(fd, attributes);
    assert(result == 0 && S_ISREG(attributes->st_mode) && attributes->st_nlink == 2);
    if (fixture->fault == Fault::ATTRIBUTES) ++attributes->st_ino;
    fixture->clock += 100 + (target ? 5 : 0) + (fixture->name == "init" ? 10 : 0) +
        (condition == 3 ? 50 : 0);
    if (fixture->fault == Fault::BUDGET && fixture->calls > kInitRcIterations * 8)
        fixture->clock += 10'000'000;
    if ((fixture->fault == Fault::CLEANUP || fixture->fault == Fault::DOUBLE_FAILURE) && fixture->leftover.empty()) {
        fixture->leftover = path.substr(0, path.find_last_of('/')) + "/extra";
        const int extra = open(fixture->leftover.c_str(), O_CREAT | O_EXCL | O_WRONLY, 0600);
        assert(extra >= 0);
        close(extra);
    }
    if (fixture->fault == Fault::DOUBLE_FAILURE) { errno = EACCES; return -1; }
    return result;
}
const InitRcTimingIo io{set_name, get_name, pin_cpu, cpu, stat_fd, now};
int entries(const char *directory) {
    auto *dir = opendir(directory);
    assert(dir);
    int count = 0;
    while (auto *entry = readdir(dir)) {
        if (std::strcmp(entry->d_name, ".") && std::strcmp(entry->d_name, "..")) ++count;
    }
    closedir(dir);
    return count;
}
InitRcTimingResult collect(const char *directory, Fixture &value) {
    fixture = &value;
    return collect_initrc_timing(directory, &io);
}
void success(const char *directory) {
    Fixture state;
    const auto result = collect(directory, state);
    assert(result.stage == InitRcTimingStage::COLLECTED && result.count == 32);
    assert(result.cleanup_complete && result.cleanup_error == 0 && entries(directory) == 0);
    assert(state.worker_only && state.name == "original");
    assert(state.calls == 33 * 4 * kInitRcIterations);
    for (const auto &round : result.rounds) {
        assert(round[0] == 100 * kInitRcIterations && round[1] == 105 * kInitRcIterations);
        assert(round[2] == 110 * kInitRcIterations && round[3] == 165 * kInitRcIterations);
    }
    int positions[4][4]{};
    std::map<std::pair<int, int>, int> transitions;
    for (std::size_t round = 0; round < kInitRcRounds; ++round) {
        for (int slot = 0; slot < 4; ++slot) {
            const auto index = (round + 1) * 4 + slot; // skip warm-up
            ++positions[slot][state.conditions[index]];
            if (slot) ++transitions[{state.conditions[index - 1], state.conditions[index]}];
        }
    }
    for (auto &position : positions) for (int count : position) assert(count == 8);
    assert(transitions.size() == 12);
    for (const auto &transition : transitions) assert(transition.second == 8);
}
void failures(const char *directory) {
    const std::pair<Fault, InitRcTimingStage> cases[] = {
        {Fault::NAME, InitRcTimingStage::NAME_FAILED}, {Fault::READBACK, InitRcTimingStage::NAME_FAILED},
        {Fault::PIN, InitRcTimingStage::AFFINITY_FAILED}, {Fault::CPU, InitRcTimingStage::AFFINITY_FAILED},
        {Fault::CPU_DENIED, InitRcTimingStage::AFFINITY_FAILED},
        {Fault::CLOCK, InitRcTimingStage::CLOCK_FAILED}, {Fault::STAT, InitRcTimingStage::SYSCALL_FAILED},
        {Fault::ATTRIBUTES, InitRcTimingStage::SYSCALL_FAILED}, {Fault::BUDGET, InitRcTimingStage::TIMED_OUT},
    };
    for (const auto &test : cases) {
        Fixture state;
        state.fault = test.first;
        const auto result = collect(directory, state);
        assert(result.stage == test.second && result.error != 0 && result.count < 32);
        assert(result.cleanup_complete && entries(directory) == 0 && state.worker_only);
        if (test.first == Fault::CPU_DENIED) assert(result.error == EPERM);
        assert(state.name == "original");
    }
}
void cleanup_failure(const char *directory, bool double_failure) {
    Fixture state;
    state.fault = double_failure ? Fault::DOUBLE_FAILURE : Fault::CLEANUP;
    const auto result = collect(directory, state);
    assert(!result.cleanup_complete && result.cleanup_error == ENOTEMPTY);
    assert(result.stage == (double_failure ? InitRcTimingStage::SYSCALL_FAILED : InitRcTimingStage::CLEANUP_FAILED));
    if (double_failure) assert(result.error == EACCES);
    assert(unlink(state.leftover.c_str()) == 0);
    assert(rmdir(state.leftover.substr(0, state.leftover.find_last_of('/')).c_str()) == 0);
    assert(entries(directory) == 0);
}
}  // namespace

int main() {
    char directory[] = "/tmp/initrc-timing-test-XXXXXX";
    assert(mkdtemp(directory));
    success(directory);
    failures(directory);
    cleanup_failure(directory, false);
    cleanup_failure(directory, true);
    Fixture state;
    assert(collect("relative", state).stage == InitRcTimingStage::FILE_FAILED);
    assert(collect("/nonexistent/initrc-timing-test", state).stage == InitRcTimingStage::FILE_FAILED);
    assert(collect_initrc_timing(directory).stage == InitRcTimingStage::UNSUPPORTED_ABI);
    assert(entries(directory) == 0 && rmdir(directory) == 0);
}
