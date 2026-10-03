# SPDX-License-Identifier: Apache-2.0
"""Configure offline documentation for the installed quality-gate package."""

project = "Relay quality evidence"
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.doctest",
]
nitpicky = True
autodoc_member_order = "bysource"
napoleon_numpy_docstring = False
html_theme = "classic"
templates_path = ["_templates"]
exclude_patterns = ["_build"]
