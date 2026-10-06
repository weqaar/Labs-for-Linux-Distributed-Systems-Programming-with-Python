Build and inspect the site
==========================

From the lab root, install the project and its development extra, then build
both outputs. Dependencies have one home, ``pyproject.toml``.

.. code-block:: console

   python -m pip install -e ".[dev]"
   python -m sphinx -n -W --keep-going -b html docs build/docs/html
   python -m sphinx -n -W --keep-going -b doctest docs build/docs/doctest
   python -m http.server 8000 --bind 127.0.0.1 --directory build/docs/html

Visit http://127.0.0.1:8000/ and stop the foreground server with Ctrl-C. The
server is a loopback preview, not a production deployment. Serve only the
generated HTML directory, never the repository or an authenticated API.

Sphinx reads ``conf.py``, parses the toctree, imports the API using autodoc and
resolves symbolic references before writing HTML. Autosummary supplies the API
table. Napoleon converts Google-style docstring sections into documentation
nodes. The doctest builder executes examples and compares their output; an HTML
build alone does not execute them. ``-n`` enables nitpicky references, ``-W``
makes warnings fail the command, and ``--keep-going`` collects more diagnostics
without turning the result into success.

The built-in classic theme needs no theme package or remote inventory.
``_templates/layout.html`` extends ``!layout.html`` to add lab guidance
after the original footer. The exclamation mark selects the theme's original
template instead of recursively extending this override. Keep the change small
and review the rendered footer after a Sphinx upgrade.

Write the contract next to the code
===================================

Inspect ``binary.py`` in a REPL with ``help(executable_format)``. Its docstring
defines what the signature result does and does not promise. The type hint
alone says neither that the file is opened nor that invalid signatures return
UNKNOWN. Its inline comment explains why the PE offset must be read, not merely
that ``seek`` changes the file position.

Exercise the workflow by documenting the distinction between a missing input
file and a missing ``readelf`` command. Add a deterministic example for an
unknown signature, then check the rendered exception section and run both
builders. Avoid examples that launch arbitrary input executables.

Autodoc imports execute code
============================

Moving a client connection to module scope makes importing documentation
contact the network too. The same applies to reading credentials, parsing CLI
arguments and starting worker threads. Put resource creation behind an explicit
call and a typed adapter; keep command startup under a main guard.

The documentation tests create an isolated module. Importing that module raises
``RuntimeError``. The tests then ask autodoc to import it. A strict build must fail. This
safe substitute checks how Sphinx handles import errors without making a real network
request. Mocking every failed import would hide this architectural mistake.

Compare with MkDocs
===================

Sphinx is the required API and executable-example workflow here. MkDocs is a
useful comparison for a Markdown-first guide: its core builds navigation and
pages but does not replace autodoc or the doctest builder. API plugins bring
their own dependency and import behavior.

An optional self-contained guide lives in ``mkdocs/``. Run it from the lab root:

.. code-block:: console

   python -m pip install -e ".[dev,docs-comparison]"
   python -m mkdocs build --strict --config-file mkdocs.yml
   python -m mkdocs serve --dev-addr 127.0.0.1:8001 --config-file mkdocs.yml

Compare source markup, navigation and API maintenance with the Sphinx output,
then stop the preview. This optional site is not part of the default gate.
