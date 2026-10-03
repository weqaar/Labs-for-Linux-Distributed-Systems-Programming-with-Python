Inspect bytes without launching them
====================================

The small ELF prefix below is deliberately not a runnable program. Signature
recognition returns a format, not a guarantee of a complete executable.
The example creates its file in the build's current directory and removes it.

.. testsetup::

   from pathlib import Path
   from tempfile import TemporaryDirectory
   from lab_02_package_build.binary import executable_format
   workspace = TemporaryDirectory(dir=".")
   candidate = Path(workspace.name) / "relayctl"

.. doctest::

   >>> candidate.write_bytes(b"\x7fELF")
   4
   >>> executable_format(candidate).value
   'ELF'
   >>> candidate.write_bytes(b"not an executable")
   17
   >>> executable_format(candidate).value
   'unknown'

.. testcleanup::

   workspace.cleanup()

The pytest documentation test changes the expected ``'ELF'`` to ``'PE'`` only
in a scratch copy. A nonzero doctest build proves the example is executed.
