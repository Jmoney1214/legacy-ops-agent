# Standard governed agent template

Copy this directory, rename it to the new `agent_id`, update every manifest field,
replace the instructions, and replace the generic evals with task-specific cases.

A new agent cannot reach production until its exact artifact digest passes contract,
tool, guardrail, unit-test, eval, code-review, and staging gates and an owner approves
the production release.
