Relay quality evidence
======================

A clean-looking pipeline can mean its checker never ran. This checkpoint
preserves pass, fail and error evidence for the relay task service and the
eventual SigRaft release.

.. toctree::
   :maxdepth: 2

   workflow
   api
   examples

Read :doc:`workflow` before treating :attr:`~lab_03_quality_gate.quality.GateReport.exit_code`
as evidence. The result is meaningful only if the intended checks ran.
