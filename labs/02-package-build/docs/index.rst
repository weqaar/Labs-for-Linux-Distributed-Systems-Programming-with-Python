Relay package build
===================

A command with a familiar filename can still contain the wrong executable.
This lab packages ``relayctl`` and inspects the bytes before release.
Here ``relayctl`` inspects executables; it does not contact SigRaft or submit
jobs. The Copier template separately generates the ``/tasks`` job fields
used in later labs.

.. toctree::
   :maxdepth: 2

   workflow
   api
   examples

Start with :doc:`workflow`, then compare :func:`~lab_02_package_build.binary.executable_format`
with the stronger tool-based inspection in :doc:`api`.
