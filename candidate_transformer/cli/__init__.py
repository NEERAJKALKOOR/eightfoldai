"""Thin command-line interface.

The CLI is a thin shell over the engine library: it wires file paths and a
projection config into ``TransformerEngine`` and serializes the result. It
imports the engine; the engine never imports the CLI.
"""
