Prove an honest verdict
=======================

Use a fake at the process boundary, not a mock of the function being tested.
This example does not run external quality tools or need a cloud account.

.. testsetup::

   from lab_03_quality_gate.quality import (
       GateCommand, GateReport, ProcessResult, run_gate,
   )

.. doctest::

   >>> class FindingRunner:
   ...     def run(self, argv: tuple[str, ...]) -> ProcessResult:
   ...         return ProcessResult(1, stdout="one finding")
   >>> command = GateCommand("lint", ("ruff", "check"), "Check relay source")
   >>> result = run_gate(command, FindingRunner())
   >>> result.outcome.value
   'failed'
   >>> report = GateReport("relay-quality", (result,))
   >>> report.failures, report.errors, report.exit_code
   (1, 0, 1)
   >>> "<failure" in report.to_junit_xml()
   True

These are the lab's modelled status conventions. A tool with different exit
semantics needs a different adapter, not an assumption that every 1 is a finding.
