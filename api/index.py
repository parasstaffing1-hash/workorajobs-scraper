"""Workora Jobs - Vercel Serverless Entry Point"""
import sys, os, pathlib
_root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

from scripts.workora_app import app  # noqa: F401 — Vercel reads this `app`
