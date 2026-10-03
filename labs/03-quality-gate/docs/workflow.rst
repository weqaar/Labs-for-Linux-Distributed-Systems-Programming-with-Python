Make documentation a measured check
===================================

From this lab's root, build the installed package's API and execute its examples:

.. code-block:: console

   python -m pip install -e ".[dev]"
   python -m sphinx -n -W --keep-going -b html docs build/docs/html
   python -m sphinx -n -W --keep-going -b doctest docs build/docs/doctest
   python -m http.server 8000 --bind 127.0.0.1 --directory build/docs/html

Open http://127.0.0.1:8000/ and stop the foreground preview with Ctrl-C. It is
not an authenticated production service. Binding only to loopback avoids
advertising the preview on other interfaces.

Sphinx imports ``quality.py`` with autodoc, indexes its API objects, converts
Google-style docstrings with napoleon and adds autosummary tables. The classic
theme and small ``_templates/layout.html`` override supply the presentation.
``!layout.html`` selects the original theme template; ``super()`` retains its
footer. No remote theme or object inventory is needed.

The HTML builder resolves the toctree and references. The doctest builder
executes examples, including doctests in imported docstrings. Always run both.
Nitpicky mode checks missing object targets, ``-W`` fails on warnings and
``--keep-going`` gathers remaining diagnostics while preserving failure.

The pytest gate calls these actual builders. In separate scratch copies it
introduces an unknown reference and changes an expected verdict to a lie.
Each build must return nonzero and name the relevant defect. The gate also
serves generated HTML on an ephemeral loopback port and retrieves it before
closing the server. It never edits the checked-in documentation to seed failure.

Contracts, layout and annotations
=================================

PEP 8 describes Python style, including a recommended 79-character code limit.
This repository deliberately configures 100 characters for consistent lab
layout. Ruff formatting plus selected lint checks enforce that local policy;
neither claims complete PEP 8 certification.

PEP 257 describes docstring conventions: a summary line, a blank line before
details, and a contract a caller can use. Google-style ``Args``, ``Returns``
and ``Raises`` sections are an additional presentation convention, not PEP 257
itself. Ruff's selected pydocstyle rules apply only to ``quality.py`` here;
they cannot decide whether an exception promise is true.

PEP 484 describes function and type annotations; PEP 526 adds variable annotation
syntax. Neither performs runtime validation:

.. code-block:: python

   def label(exit_code: int) -> str:
       """Describe a completed check's status without running it."""
       ...

   attempts: int = 0

An ``int`` hint does not specify allowed status values. ``run_gate`` documents
the mapping and its missing-command behavior, while examples and tests check
it. The comment in ``GateReport.exit_code`` explains why errors take priority,
not that an if-statement tests a condition.

Exercises
=========

Use ``help(run_gate)`` and ``inspect.signature(run_gate)`` in the installed
package. Explain which contract facts appear in the docstring but not the
signature. Add a missing-command example using a deterministic fake that raises
``FileNotFoundError``, then verify both the error count and JUnit ``<error>``.
Expand the ``to_junit_xml`` docstring to explain what information callers can
retain. Add an inline comment only where a policy decision needs a reason.

Next, copy ``docs`` into a directory under ``build/`` and change an expected
output or a ``:func:`` target there. Run the matching builder and inspect its
nonzero status. Do not weaken ``nitpicky`` or suppress the warning to get green.
Restore the contract, not the appearance of success.

Autodoc runs imports
====================

An import that starts a worker or asks for credentials also does so when Sphinx
builds the reference. Defer resource acquisition to an explicit callable.
The tests demonstrate this safely with a scratch module that raises on import;
the strict build must reject it. An HTML build is code execution, so review
extensions, ``conf.py`` and imported modules as executable inputs.

An optional Markdown comparison
===============================

MkDocs core is suited to Markdown guides and navigation. Sphinx supplies the
Python object references, autodoc and doctest path required by this checkpoint.
Plugins can extend MkDocs, but that is a separate dependency and test decision.

.. code-block:: console

   python -m pip install -e ".[dev,docs-comparison]"
   python -m mkdocs build --strict --config-file mkdocs.yml
   python -m mkdocs serve --dev-addr 127.0.0.1:8001 --config-file mkdocs.yml

Compare the optional guide with the generated API site, then stop the preview.
The optional MkDocs build is not part of ``pybootstrap check``.
