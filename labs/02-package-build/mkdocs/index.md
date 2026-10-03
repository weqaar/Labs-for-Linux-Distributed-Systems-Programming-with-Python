# Inspect a relay release

This optional Markdown-first page describes the same checkpoint as the Sphinx
site. Build the command, inspect its ELF or PE signature, and test it on the
supported target. A signature alone does not prove safety or compatibility.

```bash
relayctl inspect dist/relayctl
```

MkDocs core renders this listing but does not execute it or import the Python
API. Compare it with Sphinx's generated `executable_format` reference and
doctest output. Change a function's contract and observe which pages update
automatically and which need a manual edit.
