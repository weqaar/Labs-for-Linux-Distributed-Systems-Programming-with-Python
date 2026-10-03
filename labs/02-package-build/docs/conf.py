# SPDX-License-Identifier: Apache-2.0
"""Configure an offline documentation build of the installed lab package."""

project = "Relay package build"
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.doctest",
]
nitpicky = True
# The standard-library inventory is remote; ignore only this external type.
nitpick_ignore = [("py:class", "os.PathLike")]
autodoc_member_order = "bysource"
napoleon_numpy_docstring = False
html_theme = "classic"
templates_path = ["_templates"]
exclude_patterns = ["_build"]
