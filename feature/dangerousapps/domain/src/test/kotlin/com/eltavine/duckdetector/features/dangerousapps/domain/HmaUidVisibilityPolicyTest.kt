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

package com.eltavine.duckdetector.features.dangerousapps.domain

import org.junit.Assert.assertEquals
import org.junit.Test

class HmaUidVisibilityPolicyTest {
    private val baseline = HmaUidVisibilityObservation(true, 10000, setOf(10042, 10043), 10042,
        true, true, emptyList(), false, false)

    @Test fun `a completed scan must contain the live caller`() {
        val result = HmaUidVisibilityPolicy.evaluate(baseline.copy(taskUids = setOf(10043), unassociated = listOf(10043)))
        assertEquals(HmaCheckState.INCONCLUSIVE, result.state)
        assertEquals(emptyList<Int>(), result.uids)
    }

    @Test fun `failed association controls suppress foreign UID claims`() {
        val result = HmaUidVisibilityPolicy.evaluate(baseline.copy(callerAssociationAfter = false, unassociated = listOf(10043)))
        assertEquals(HmaCheckState.INCONCLUSIVE, result.state)
        assertEquals(emptyList<Int>(), result.uids)
    }

    @Test fun `a failed repeat is inconclusive and a bounded prefix remains partial`() {
        assertEquals(HmaCheckState.INCONCLUSIVE, HmaUidVisibilityPolicy.evaluate(baseline.copy(verificationFailed = true)).state)
        assertEquals(HmaCheckState.PARTIAL, HmaUidVisibilityPolicy.evaluate(baseline.copy(complete = false)).state)
        assertEquals(HmaCheckState.PARTIAL, HmaUidVisibilityPolicy.evaluate(baseline.copy(verificationLimitReached = true)).state)
    }

    @Test fun `valid positive evidence survives a partial search without claiming package identity`() {
        val result = HmaUidVisibilityPolicy.evaluate(baseline.copy(complete = false, unassociated = listOf(10043)))
        assertEquals(HmaCheckState.INCONSISTENT, result.state)
        assertEquals(listOf(10043), result.uids)
        assertEquals(emptyList<String>(), result.packages)
    }

    @Test fun `complete controlled matching associations are consistent within scope`() {
        assertEquals(HmaCheckState.CONSISTENT, HmaUidVisibilityPolicy.evaluate(baseline).state)
    }
}
