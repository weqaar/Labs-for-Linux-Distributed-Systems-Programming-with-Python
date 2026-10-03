# Interpret relay quality evidence

The checkpoint keeps three verdicts distinct. Passed means a check completed
without findings. Failed means it completed and found a problem. Errored means
it did not complete a valid check. Missing evidence cannot become success.

Run `pybootstrap check` from the lab root. A zero exit requires every configured
gate, including the Sphinx builds and negative controls executed by pytest.

This optional MkDocs page is handwritten Markdown. Its listings are not
automatically executed. Compare a change to `run_gate` with the Sphinx API:
which documentation follows the source, and which requires an explicit edit?
