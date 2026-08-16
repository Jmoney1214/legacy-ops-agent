from __future__ import annotations

import copy
import unittest

from scripts import validate_build_control_pack as validator


class BuildControlValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = validator.load_yaml(validator.CATALOG_PATH)
        cls.status = validator.load_yaml(validator.STATUS_PATH)
        cls.phases = validator.validate_catalog(cls.catalog)

    def test_current_control_files_are_valid(self) -> None:
        validator.validate_status(copy.deepcopy(self.status), self.phases)

    def test_not_applicable_gate_requires_reason(self) -> None:
        status = copy.deepcopy(self.status)
        status["phases"]["P00"]["gate_exceptions"].pop("staging_deployed")
        with self.assertRaisesRegex(validator.ValidationError, "not_applicable without a reason"):
            validator.validate_status(status, self.phases)

    def test_program_and_current_phase_status_must_match(self) -> None:
        status = copy.deepcopy(self.status)
        status["program"]["status"] = "pr_draft"
        with self.assertRaisesRegex(validator.ValidationError, "Program status must match"):
            validator.validate_status(status, self.phases)

    def test_boolean_gate_state_is_rejected(self) -> None:
        status = copy.deepcopy(self.status)
        status["phases"]["P00"]["gates"]["ci_green"] = True
        with self.assertRaisesRegex(validator.ValidationError, "Invalid gate states"):
            validator.validate_status(status, self.phases)

    def test_ready_to_merge_requires_satisfied_pre_merge_gates(self) -> None:
        status = copy.deepcopy(self.status)
        status["program"]["status"] = "ready_to_merge"
        status["phases"]["P00"]["status"] = "ready_to_merge"
        status["program"]["merge_authorized"] = True
        status["phases"]["P00"]["merge_authorized"] = True
        with self.assertRaisesRegex(validator.ValidationError, "unsatisfied gates"):
            validator.validate_status(status, self.phases)

    def test_complete_requires_post_merge_gates(self) -> None:
        status = copy.deepcopy(self.status)
        status["program"]["status"] = "complete"
        status["phases"]["P00"]["status"] = "complete"
        status["program"]["merge_authorized"] = True
        status["phases"]["P00"]["merge_authorized"] = True
        with self.assertRaisesRegex(validator.ValidationError, "unsatisfied gates"):
            validator.validate_status(status, self.phases)


if __name__ == "__main__":
    unittest.main()
