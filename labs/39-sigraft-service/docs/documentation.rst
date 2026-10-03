Maintaining and releasing the documentation
===========================================

Build a complete site, treating warnings as errors, then execute its examples::

   python -m sphinx -n -W --keep-going -b html docs build/docs/html
   python -m sphinx -W -b doctest docs build/docs/doctest
   python -m http.server --bind 127.0.0.1 --directory build/docs/html 8000

Open ``http://127.0.0.1:8000/`` and inspect navigation, search, the Python API
and the version in the footer. Stop the server with Ctrl+C. This is a local
preview, not the production hosting service.

The small template override extends the built-in theme rather than copying it.
It adds a version reminder, while the theme retains navigation and styling.
The release number comes from installed package metadata; build the docs in
the same environment as the package being released.

The test gate invokes the HTML and doctest builders in a temporary directory,
serves the output through a loopback HTTP server and retrieves its index.
Negative fixtures introduce an unresolved reference and a wrong expected result.
Each must fail its build. This establishes that the check rejects broken
documentation, not only that the original site builds.

.. doctest::

   >>> from lab_39_sigraft_service.sigraft_service import SigRaftService
   >>> service = SigRaftService("sha256:" + "a" * 64)
   >>> job = service.submit_task("count failed requests")
   >>> (job.task_id, job.state)
   ('task-1', 'queued')
   >>> service.transition_task(job.task_id, "running").state
   'running'
   >>> service.transition_task(job.task_id, "succeeded").state
   'succeeded'
   >>> service.shutdown()

Documentation exercise
----------------------

Extend one public method's docstring with its input constraints, result,
exceptions and a deterministic example. Use the installed package's ``help``
output before looking at the HTML. Add a comment only where it explains a
non-obvious invariant, such as atomic snapshot registration, not what an
obvious assignment does.

Explain which properties type annotations express and which require prose or
runtime validation. PEP 8 guides code style, PEP 257 guides docstrings, and
PEP 484 defines type hints. Ruff formatting and selected lint rules automate
parts of this policy; a passing formatter does not certify design quality or
complete compliance with every PEP.

Introduce a wrong expected state in a temporary copy of this doctest and
observe a nonzero exit status. Restore the correct expectation, rebuild both
outputs and run ``pybootstrap check``. Never suppress a docs-builder failure
to publish an apparently successful release.

Release artifact
----------------

After building the package and installing that exact version, build the site
once and archive ``build/docs/html`` alongside the package and its release
evidence. Publish it under a version-specific path on a static host. Promote
the same archive rather than regenerating it independently in production.
The Azure example retains the site as a pipeline artifact; the on-prem plan
creates a version-labelled archive for the hosting adapter to publish.

External link checking is a separate network-dependent operation::

   python -m sphinx -W -b linkcheck docs build/docs/linkcheck

It cannot replace local reference checks or doctests. The required local gate
does not depend on external sites being available.
