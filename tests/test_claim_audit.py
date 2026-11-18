import unittest
from claim_audit import compare, validate, REQUIRED, rounded_count_interval, transition_matrix, joint_binary_partition, paired_marginal_bounds, portfolio_audit


def claim():
    item = dict.fromkeys(REQUIRED, "example")
    item.update(source="https://example.org/paper", denominator=10, reported=2,
                derived=None, human_eligibility=None, human_claim_adjudication=None)
    return item


class ClaimAuditTests(unittest.TestCase):
    def test_portfolio_complementarity_and_bounds(self):
        u = ["a", "b", "c", "d"]
        p = portfolio_audit(u, {"A": dict(zip(u, ["success", "success", "error", "non_success"])),
                                "B": dict(zip(u, ["success", "non_success", "missing", "success"]))})
        self.assertEqual((p["union_credited"], p["union_upper"], p["intersection_credited"], p["intersection_upper"]), (3, 4, 1, 2))
        self.assertEqual(p["multiplicity"], {"0": 1, "1": 2, "2": 1})
        self.assertEqual(p["unique_success_ids"], {"A": ["b"], "B": ["d"]})
        self.assertEqual((p["pairs"][0]["turnover"], p["pairs"][0]["joint_known"]), (2, 3))

    def test_portfolio_rejects_unaligned_or_invalid(self):
        for configs in ({"a": {"x": "success"}}, {"a": {"x": "success", "y": True}},
                        {"a": {"x": "success", "y": "missing", "z": "success"}}):
            with self.assertRaises(ValueError):
                portfolio_audit(["x", "y"], configs)
        with self.assertRaises(ValueError):
            portfolio_audit(["x","x"], {"a":{"x":"success"}})

    def test_portfolio_single_and_all_unknown(self):
        p = portfolio_audit(["x"], {"a":{"x":"missing"}})
        self.assertEqual((p["union_credited"],p["union_upper"],p["intersection_credited"],p["intersection_upper"]), (0,1,0,1))
        self.assertEqual(p["pairs"], [])
        self.assertEqual(p["unique_success_ids"], {"a":[]})
    def test_marginal_bounds_example(self):
        self.assertEqual(paired_marginal_bounds(500,327,251), dict(both=[78,251], losses=[76,249], gains=[0,173], turnover=[76,422]))

    def test_marginal_bounds_exhaustive(self):
        for n in range(1,9):
            for a in range(n+1):
                for b in range(n+1):
                    feasible=[i for i in range(n+1) if min(i,a-i,b-i,n-a-b+i)>=0]
                    result=paired_marginal_bounds(n,a,b)
                    self.assertEqual(result['both'],[min(feasible),max(feasible)])
                    self.assertEqual(result['turnover'],[a+b-2*max(feasible),a+b-2*min(feasible)])

    def test_marginal_bounds_invalid(self):
        for args in [(0,0,0),(5,-1,2),(5,6,2),(5,True,2),(5,1,1.5)]:
            with self.assertRaises(ValueError):
                paired_marginal_bounds(*args)
    def test_validation_keeps_pending(self):
        report = validate([claim()])
        self.assertEqual(report["human_claim_adjudication_pending"], 1)

    def test_duplicate(self):
        with self.assertRaises(ValueError):
            validate([claim(), claim()])

    def test_bad_denominator(self):
        for n in (False, 0, -1, 2.5):
            with self.assertRaises(ValueError):
                validate([dict(claim(), denominator=n)])

    def test_unknown_denominator(self):
        self.assertEqual(validate([dict(claim(), denominator=None)])["null_denominator"], 1)

    def test_paired_and_missing(self):
        combinations = [(True, True), (True, False), (False, True), (False, True),
                        (False, False), (None, True), (True, None), (None, None)]
        rows = [dict(instance_id=str(i), left=a, right=b) for i, (a,b) in enumerate(combinations)]
        result = compare(rows)
        self.assertEqual(result["paired"], 5)
        self.assertEqual(result["right_minus_left_pp"], 20)
        self.assertEqual(result["cells"]["both_missing"], 1)

    def test_no_complete_pairs(self):
        self.assertIsNone(compare([dict(instance_id="x", left=None, right=False)])["right_minus_left_pp"])

    def test_nonboolean_not_accepted(self):
        with self.assertRaises(ValueError):
            compare([dict(instance_id="x", left=1, right=False)])

    def test_duplicate_instance(self):
        row = dict(instance_id="x", left=True, right=False)
        with self.assertRaises(ValueError):
            compare([row, row])

    def test_rounding_unique(self):
        self.assertEqual(rounded_count_interval("17.67", 300, 2)["unique"], 53)

    def test_rounding_ambiguous_and_impossible(self):
        self.assertGreater(rounded_count_interval("50", 1000, 0)["candidate_count"], 1)
        self.assertEqual(rounded_count_interval("12.34", 3, 2)["candidate_count"], 0)

    def test_rounding_endpoint_and_bad_input(self):
        self.assertEqual(rounded_count_interval("100", 3, 0)["unique"], 3)
        with self.assertRaises(ValueError):
            rounded_count_interval("101", 3, 0)

    def test_transition_matrix(self):
        out = transition_matrix([[1,2,0,0],[0,1,0,0],[3,0,0,0],[0,0,1,2]], 10)
        self.assertEqual((out["gains"],out["losses"],out["net_pp"]), (3,1,20))
        self.assertEqual(out["turnover_pct"], 40)

    def test_transition_wrong_total_or_cell(self):
        for matrix in ([[0]*4]*4, [[True]*4]*4):
            with self.assertRaises(ValueError):
                transition_matrix(matrix, 1)

    def test_joint_partition(self):
        out = joint_binary_partition([113,297,39,1,0],450)
        self.assertEqual((out["left_pass"],out["right_pass"],out["both"]), (410,114,113))
        self.assertAlmostEqual(out["gap_pp"],66)

    def test_joint_missing_separate(self):
        out = joint_binary_partition([0,0,1,0,1],2)
        self.assertIsNone(out["right_given_left_pct"])
        self.assertEqual(out["neither"],1)
        self.assertEqual(out["missing"],1)

    def test_joint_invalid(self):
        for cells in ([1,0,0,0,0], [True,1,0,0,0], [3,-1,0,0,0]):
            with self.assertRaises(ValueError):
                joint_binary_partition(cells,2)
