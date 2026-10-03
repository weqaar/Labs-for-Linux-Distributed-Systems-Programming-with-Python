# SPDX-License-Identifier: Apache-2.0
"""Build the versioned, offline-checkable SigRaft service documentation."""

from importlib.metadata import version as package_version

project = "SigRaft"
release = package_version("lab-39-sigraft-service")
version = release
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.doctest",
]
autosummary_generate = True
autodoc_typehints = "none"
nitpicky = True
html_theme = "alabaster"
templates_path = ["_templates"]
exclude_patterns = ["_build"]
html_title = f"{project} {release}"
