"""Compatibility entry point for Streamlit deployments still configured to dashboard/app.py."""
import app  # noqa: F401 — root app initializes the Streamlit page on import.
