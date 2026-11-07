"""Owned local journals test protected full-cycle capacity, not provider recall."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / "assets/agent-assets/skills/retrospective-research/scripts/research_budget.py"
SPEC = importlib.util.spec_from_file_location("allocation_budget_fixture", MODULE)
BUDGET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUDGET)
ALLOCATION = BUDGET.allocations


class ResearchAllocationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix="research-allocation-test-")
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        (self.root / "operations").mkdir()
        (self.root / "contracts").mkdir()
        self.contract = {"requirements": {"research_budget": True, "protected_followups": True},
                         "research_budget": {"search_attempts": 20, "read_attempts": 20,
                                             "assessed_artifacts": 20}, "questions": []}
        self.save_contract()
        BUDGET.initialize(self.root)
        self.now = 1000.0
        self.clock = patch.object(BUDGET.time, "time", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def save_contract(self):
        raw = json.dumps(self.contract).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (self.root / "contract.json").write_bytes(raw)
        (self.root / "contracts" / (digest + ".json")).write_bytes(raw)
        (self.root / "bundle.json").write_text(json.dumps({"contract_sha256": digest}))

    def amend(self, **limits):
        changed = copy.deepcopy(self.contract)
        changed["research_budget"].update(limits)
        BUDGET.validate_amendment(self.contract, changed, self.state())
        self.contract = changed
        self.save_contract()

    def state(self):
        return BUDGET.load(self.root / "research-budget.json")

    def start(self, actor="reader", allocation="baseline", purpose="research", branch="inputs"):
        return BUDGET.activity(self.root, actor, "start", allocation=allocation, purpose=purpose, branch=branch)

    def stop(self, actor="reader"):
        return BUDGET.activity(self.root, actor, "stop")

    def spend(self, minutes):
        self.now += minutes * 60

    def spec(self, key, kind="search", parent=None, generation=0, actor="reader", allocation=None,
             source_id=None, assess=True, branch="inputs"):
        allocation = allocation or ("baseline" if generation == 0 else "followup-" + str(generation))
        request = {"query": key, "filters": {}, "cap": 10, "cursor": None} if kind == "search" else {
            "source_id": source_id or key, "format": "text", "extent": "body"}
        return {"id": key, "kind": kind, "provider": "owned-fixture", "actor": actor,
                "request": request, "timeout": 240,
                "research": {"stage": "baseline" if generation == 0 else "priority" if generation == 1 else "deep",
                             "allocation": allocation, "branch": branch, "parent_operation": parent,
                             "root_basis": "Explicit fixture baseline", "assess": assess if kind == "read" else False}}

    def admit(self, spec, source_ids=()):
        def create():
            path = self.root / "operations" / spec["id"]
            path.mkdir()
            request = {"spec": spec, "contract_sha256": BUDGET.load(self.root / "bundle.json")["contract_sha256"]}
            (path / "request.json").write_text(json.dumps(request))
            (path / "receipt.json").write_text(json.dumps({"outcome": "ok"}))
            (path / "result.json").write_text(json.dumps({"source_ids": list(source_ids)}))
            return spec
        return BUDGET.admit(self.root, spec, create)

    def seed(self):
        self.start()
        self.admit(self.spec("seed", "read", source_id="trunk"), ["trunk"])
        self.stop()

    def first_cycle(self, actor="reader"):
        self.start(actor, "followup-1")
        self.admit(self.spec("term", parent="seed", generation=1, actor=actor), ["content"])
        self.admit(self.spec("content-read", "read", parent="term", generation=1, actor=actor,
                             source_id="content"), ["content"])
        self.stop(actor)

    def checkpoint(self, key="completed-round"):
        return {"id": key, "sha256": "a" * 64}

    def test_protected_defaults_and_explicit_caps_preserve_legacy(self):
        self.assertEqual(BUDGET.policy(self.contract)["active_minutes"], 330)
        self.assertEqual(BUDGET.policy(self.contract)["agent_minutes"], 600)
        self.contract["research_budget"]["active_minutes"] = 500
        self.assertEqual(BUDGET.policy(self.contract)["active_minutes"], 500)
        self.contract["requirements"].pop("protected_followups")
        self.contract["research_budget"].pop("active_minutes")
        self.assertEqual(BUDGET.policy(self.contract)["active_minutes"], 120)
        self.assertEqual(BUDGET.policy(self.contract)["agent_minutes"], 300)

    def test_invalid_shares_rounds_and_flags_are_rejected(self):
        for configuration in ({"baseline_percent": 40}, {"baseline_percent": 0, "extension_percent": 50},
                              {"minimum_followup_rounds": True}, {"minimum_followup_rounds": 1},
                              {"minimum_followup_rounds": 11}, {"followup_percent": float("nan")},
                              {"unknown": 1}):
            with self.subTest(configuration=configuration):
                self.contract["research_allocation"] = configuration
                with self.assertRaises(ValueError):
                    BUDGET.policy(self.contract)
        self.contract.pop("research_allocation")
        self.contract["requirements"]["protected_followups"] = "yes"
        with self.assertRaisesRegex(ValueError, "boolean"):
            BUDGET.policy(self.contract)

    def test_baseline_cannot_consume_followup_query_reserve(self):
        self.start()
        for i in range(10):
            self.admit(self.spec("initial-" + str(i)))
        before = (self.root / "research-budget.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "baseline/search_attempts"):
            self.admit(self.spec("one-too-many"))
        self.assertEqual(before, (self.root / "research-budget.json").read_bytes())
        status = BUDGET.status(self.root)
        self.assertEqual(status["search_attempts"], 10)
        self.assertEqual(status["protected_followups"]["buckets"]["followup-1"]["remaining"]["search_attempts"], 5)

    def test_baseline_time_stops_even_with_global_headroom(self):
        self.amend(active_minutes=10, agent_minutes=100)
        self.start()
        self.spend(5.1)
        with self.assertRaisesRegex(ValueError, "protected research time"):
            self.admit(self.spec("too-late"))
        status = BUDGET.status(self.root)
        self.assertAlmostEqual(status["active_minutes"], 5.1)
        self.assertEqual(status["global_overruns"], {})
        self.assertEqual(status["protected_followups"]["overruns"][0]["allocation"], "baseline")
        self.stop()

    def test_stopped_overrun_preserves_usable_followup_time_and_global_caps(self):
        fixture_parent = self.root
        for metric in ("active_minutes", "agent_minutes"):
            for overshoot_seconds in (0, 0.001, 6):
                with self.subTest(metric=metric, overshoot_seconds=overshoot_seconds):
                    self.root = fixture_parent / (metric + "-" + str(overshoot_seconds))
                    (self.root / "operations").mkdir(parents=True)
                    (self.root / "contracts").mkdir()
                    self.contract["research_budget"].update(active_minutes=100, agent_minutes=100)
                    self.contract["research_budget"][metric] = 10
                    self.save_contract()
                    BUDGET.initialize(self.root)
                    self.start()
                    self.spend(5 + overshoot_seconds / 60)
                    self.stop()
                    before = (self.root / "research-budget.json").read_bytes()
                    for exhausted in ("baseline", "extension"):
                        with self.assertRaisesRegex(ValueError, "protected research time"):
                            self.start("exhausted", exhausted)
                    self.assertEqual(before, (self.root / "research-budget.json").read_bytes())

                    self.start("first", "followup-1")
                    self.start("second", "followup-2")
                    status = BUDGET.status(self.root)
                    capacity = status["protected_followups"]
                    seconds = ALLOCATION.remaining_seconds(
                        "followup-1", status["limits"], status, capacity)
                    self.assertAlmostEqual(seconds, 150 - overshoot_seconds)
                    self.assertAlmostEqual(capacity["shared_pool"]["remaining"][metric],
                                           -overshoot_seconds / 60)
                    self.spend(seconds / 60)
                    self.stop("first")
                    self.stop("second")
                    after = BUDGET.status(self.root)
                    self.assertEqual(after["global_overruns"], {})
                    self.assertAlmostEqual(after["by_allocation"]["baseline"][metric],
                                           5 + overshoot_seconds / 60)
                    for allocation in ("followup-1", "followup-2"):
                        self.assertLessEqual(ALLOCATION.remaining_seconds(
                            allocation, after["limits"], after,
                            after["protected_followups"]), 1e-6)

    def test_early_followup_can_run_concurrently_with_baseline(self):
        self.seed()
        self.start("baseline-reader", "baseline")
        self.start("followup-reader", "followup-1")
        self.admit(self.spec("early-term", parent="seed", generation=1, actor="followup-reader"), ["content"])
        self.spend(2)
        self.stop("baseline-reader")
        self.spend(1)
        self.stop("followup-reader")
        totals = BUDGET.status(self.root)
        self.assertEqual(totals["active_minutes"], 3)
        self.assertEqual(totals["agent_minutes"], 5)
        self.assertEqual(totals["by_allocation"]["baseline"]["active_minutes"], 2)
        self.assertEqual(totals["by_allocation"]["followup-1"]["active_minutes"], 3)
        self.assertIn("may overlap", totals["allocation_time_basis"])

    def test_first_round_cannot_relabel_work_to_raid_second(self):
        self.seed()
        self.start(allocation="followup-2")
        with self.assertRaisesRegex(ValueError, "actual generation: followup-1"):
            self.admit(self.spec("wrong-round", parent="seed", generation=1, allocation="followup-2"))
        self.assertEqual(BUDGET.status(self.root)["search_attempts"], 0)

    def test_complete_followup_cycle_charges_query_read_and_interpretation(self):
        self.seed()
        self.start(allocation="followup-1")
        self.admit(self.spec("term", parent="seed", generation=1), ["content"])
        self.spend(1)
        self.admit(self.spec("content-read", "read", parent="term", generation=1,
                             source_id="content", assess=False), ["content"])
        BUDGET.assess(self.root, "content-read", "reader")
        self.spend(4)
        self.stop()
        self.start(allocation="followup-1", purpose="coordination")
        self.spend(2)
        self.stop()
        totals = BUDGET.status(self.root)
        spent = totals["by_allocation"]["followup-1"]
        self.assertEqual(spent["search_attempts"], 1)
        self.assertEqual(spent["read_attempts"], 1)
        self.assertEqual(spent["assessed_artifacts"], 1)
        self.assertEqual(spent["active_minutes"], 7)
        self.assertEqual(totals["by_purpose"]["research"]["agent_minutes"], 5)
        self.assertEqual(totals["by_purpose"]["coordination"]["agent_minutes"], 2)

    def test_dispatch_deadline_is_clamped_to_local_effort_and_time(self):
        self.amend(active_minutes=100, agent_minutes=10)
        self.start("one")
        self.start("two")
        self.spend(1)
        spec = self.admit(self.spec("last-half-minute", actor="one"))
        self.assertEqual(spec["timeout"], 90)
        self.spend(1.6)
        with self.assertRaisesRegex(ValueError, "protected research time"):
            self.admit(self.spec("effort-over", actor="two"))

    def test_concurrent_buckets_cannot_each_claim_the_full_shared_time_pool(self):
        self.amend(active_minutes=10, agent_minutes=100)
        self.seed()
        self.start("initial-reader")
        self.start("followup-reader", "followup-1")
        self.spend(2.5)
        # Initial bucket has .5 minutes reserved; followup-1 has none. Both
        # share two extension minutes, so 1.25 elapsed minutes exhausts it.
        spec = self.admit(self.spec("concurrent-term", parent="seed", generation=1, actor="followup-reader"))
        self.assertAlmostEqual(spec["timeout"], 75)
        self.spend(1.26)
        with self.assertRaisesRegex(ValueError, "protected research time"):
            self.admit(self.spec("pool-time-spent", actor="initial-reader"))
        totals = BUDGET.status(self.root)
        self.assertEqual(totals["protected_followups"]["buckets"]["followup-2"]["guaranteed_remaining"]["active_minutes"], 2.5)

    def test_first_round_cannot_spend_second_round_capacity_after_pool_exhaustion(self):
        self.seed()
        self.start(allocation="followup-1")
        for i in range(9):
            self.admit(self.spec("round-one-" + str(i), parent="seed", generation=1))
        with self.assertRaisesRegex(ValueError, "followup-1/search_attempts"):
            self.admit(self.spec("round-one-over", parent="seed", generation=1))
        totals = BUDGET.status(self.root)
        self.assertEqual(totals["protected_followups"]["buckets"]["followup-2"]["guaranteed_remaining"]["search_attempts"], 5)

    def test_repair_time_is_visible_and_cannot_reset_spending(self):
        self.start(purpose="repair")
        self.spend(3)
        self.stop()
        totals = BUDGET.status(self.root)
        self.assertEqual(totals["by_purpose"]["repair"]["agent_minutes"], 3)
        self.assertEqual(totals["by_allocation"]["baseline"]["active_minutes"], 3)
        self.assertEqual(totals["by_actor"]["reader"]["agent_minutes"], 3)

    def test_untimed_legacy_admissions_remain_explicitly_unmeasured(self):
        totals = BUDGET.accounting({"events": [{"id": 1, "type": "admit", "kind": "search",
                                                "actor": "old-reader", "assess": False}]}, current=0)
        self.assertEqual(totals["unmeasured_admission_events"], [1])
        self.assertEqual(totals["by_allocation"]["unclassified"]["search_attempts"], 1)

    def test_round_requirement_cannot_exceed_generation_cap(self):
        self.contract["research_allocation"] = {"minimum_followup_rounds": 4}
        with self.assertRaisesRegex(ValueError, "generation cap must permit"):
            BUDGET.policy(self.contract)
        self.contract["research_budget"]["followup_generations"] = 4
        self.assertEqual(len(ALLOCATION.shares(ALLOCATION.config(self.contract))), 6)

    def test_actor_allocation_and_branch_must_match_request(self):
        for changes in ({"allocation": "followup-1"}, {"branch": "unrelated"}):
            self.start()
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "must match actor"):
                self.admit(self.spec("mismatch", **changes))
            self.stop()
        with self.assertRaisesRegex(ValueError, "needs allocation"):
            BUDGET.activity(self.root, "reader", "start")
        with self.assertRaisesRegex(ValueError, "unknown research activity purpose"):
            self.start(purpose="misc")

    def test_assessment_reserves_are_deduplicated_globally_and_charged_once(self):
        self.start()
        self.admit(self.spec("first-read", "read", source_id="same"))
        other = self.spec("second-read", "read", source_id="same")
        other["request"]["extent"] = "comments"
        self.admit(other)
        self.assertEqual(BUDGET.status(self.root)["by_allocation"]["baseline"]["assessed_artifacts"], 1)
        self.amend(read_attempts=100)
        for i in range(9):
            self.admit(self.spec("read-" + str(i), "read"))
        with self.assertRaisesRegex(ValueError, "baseline/assessed_artifacts"):
            self.admit(self.spec("no-slot", "read"))

    def test_release_shared_pool_preserves_late_leads_and_other_reserves(self):
        self.seed()
        self.first_cycle()
        before = BUDGET.status(self.root)
        event = BUDGET.release(self.root, "followup-1", self.checkpoint())
        self.assertEqual(event["spent"]["search_attempts"], 1)
        self.start(allocation="followup-1")
        for i in range(8):
            self.admit(self.spec("late-" + str(i), parent="seed", generation=1))
        with self.assertRaisesRegex(ValueError, "followup-1/search_attempts"):
            self.admit(self.spec("pool-empty", parent="seed", generation=1))
        after = BUDGET.status(self.root)
        self.assertEqual(after["protected_followups"]["buckets"]["followup-2"]["guaranteed_remaining"],
                         before["protected_followups"]["buckets"]["followup-2"]["guaranteed_remaining"])
        self.assertEqual(after["protected_followups"]["shared_pool"]["remaining"]["search_attempts"], 0)
        self.stop()
        with self.assertRaisesRegex(ValueError, "already released"):
            BUDGET.release(self.root, "followup-1", self.checkpoint("another-checkpoint"))

    def test_release_waits_for_closed_activity_and_checkpoint_binding(self):
        self.start()
        with self.assertRaisesRegex(ValueError, "close allocation activity"):
            BUDGET.release(self.root, "baseline", self.checkpoint())
        self.stop()
        with self.assertRaisesRegex(ValueError, "checkpoint ID and SHA256"):
            BUDGET.release(self.root, "baseline", {"id": "unbound"})

    def test_cap_increase_after_release_retains_all_spending_without_double_grant(self):
        self.seed()
        self.first_cycle()
        BUDGET.release(self.root, "followup-1", self.checkpoint())
        self.start(allocation="followup-1")
        self.admit(self.spec("late-one", parent="seed", generation=1))
        old = BUDGET.status(self.root)
        self.amend(search_attempts=40)
        new = BUDGET.status(self.root)
        self.assertEqual(new["search_attempts"], old["search_attempts"])
        self.assertEqual(new["protected_followups"]["shared_pool"]["spent"]["search_attempts"], 1)
        self.assertEqual(new["protected_followups"]["shared_pool"]["remaining"]["search_attempts"], 16)
        self.assertEqual(new["protected_followups"]["buckets"]["followup-2"]["guaranteed_remaining"]["search_attempts"], 10)

    def test_amendments_cannot_erase_or_reclassify_spending(self):
        self.start()
        self.spend(1)
        self.stop()
        for change in ({"requirements": {"research_budget": True}},
                       {"research_allocation": {"baseline_percent": 20, "extension_percent": 30}},
                       {"research_allocation": {"minimum_followup_rounds": 3}},
                       {"research_budget": {"active_minutes": .1}}):
            changed = dict(self.contract, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                BUDGET.validate_amendment(self.contract, changed, self.state())
        legacy = copy.deepcopy(self.contract)
        legacy["requirements"].pop("protected_followups")
        with self.assertRaisesRegex(ValueError, "unclassified spending"):
            BUDGET.validate_amendment(legacy, self.contract, self.state())

    def test_legacy_activity_stays_unclassified_instead_of_fabricating_purpose(self):
        self.contract["requirements"].pop("protected_followups")
        self.save_contract()
        BUDGET.activity(self.root, "legacy-reader", "start")
        self.spend(2)
        BUDGET.activity(self.root, "legacy-reader", "stop")
        status = BUDGET.status(self.root)
        self.assertEqual(status["by_purpose"]["unclassified"]["agent_minutes"], 2)
        self.assertNotIn("protected_followups", status)
        self.assertEqual(status["limits"]["active_minutes"], 120)

    def test_coverage_review_uses_extension_without_relabelling_generation(self):
        self.start(allocation="extension", purpose="coverage-review")
        spec = self.spec("independent-check", allocation="extension")
        spec["research"]["purpose"] = "coverage-review"
        spec = self.admit(spec)
        self.assertEqual(spec["research"]["stage"], "baseline")
        totals = BUDGET.status(self.root)
        self.assertEqual(totals["by_generation"], {"0": 1})
        self.assertEqual(totals["by_purpose"]["coverage-review"]["search_attempts"], 1)

    def test_activity_purpose_cannot_override_the_operation_intent(self):
        self.start(allocation="extension", purpose="coverage-review")
        with self.assertRaisesRegex(ValueError, "operation purpose must match"):
            self.admit(self.spec("disguised-research", allocation="extension"))
        self.assertEqual(BUDGET.status(self.root)["search_attempts"], 0)

    def test_second_round_reads_use_their_own_reserve(self):
        self.seed()
        self.first_cycle()
        self.start(allocation="followup-2")
        self.admit(self.spec("second-term", parent="content-read", generation=2), ["deep-content"])
        self.admit(self.spec("second-read", "read", parent="second-term", generation=2,
                             source_id="deep-content"), ["deep-content"])
        totals = BUDGET.status(self.root)
        self.assertEqual(totals["by_allocation"]["followup-2"]["search_attempts"], 1)
        self.assertEqual(totals["by_allocation"]["followup-2"]["read_attempts"], 1)

    def test_concurrent_admissions_cannot_oversubscribe_last_bucket_slots(self):
        self.clock.stop()
        BUDGET.activity(self.root, "reader", "start", allocation="baseline", purpose="research", branch="inputs")
        script = """import importlib.util,json,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('budget',sys.argv[1]); b=importlib.util.module_from_spec(s); s.loader.exec_module(b)
root=Path(sys.argv[2]); spec=json.loads(sys.argv[3])
try:
 b.admit(root,spec,lambda: None)
except ValueError:
 raise SystemExit(1)
"""
        processes = [subprocess.Popen([sys.executable, "-I", "-B", "-c", script, str(MODULE), str(self.root),
                                       json.dumps(self.spec("concurrent-" + str(i)))],
                                      env={"PATH": os.defpath}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for i in range(12)]
        results = [process.communicate(timeout=20) for process in processes]
        self.assertTrue(all(not error for _, error in results))
        self.assertEqual(sum(process.returncode == 0 for process in processes), 10)
        self.assertEqual(BUDGET.status(self.root)["search_attempts"], 10)


if __name__ == "__main__":
    unittest.main()
