SigRaft job management and scheduling
=====================================

SigRaft exposes a Python web service for job submission, status inspection and
resource-aware scheduling. The labs study background processing concepts found
in Celery and RQ, and cluster allocation concepts found in Slurm. This is a
teaching implementation, not a feature-equivalent replacement.

A client might request counts of failed requests in application logs, with the
counts written to a CSV file. That operation explains what a job represents.
The current service records the requested action but does not execute it or
serve output files. Its trusted Python transition method records state changes
from an executor adapter or a lab exercise. Job state remains in process memory.

.. toctree::
   :maxdepth: 2

   usage
   protocol
   api
   documentation
