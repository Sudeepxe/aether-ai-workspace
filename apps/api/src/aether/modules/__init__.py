"""Service modules — 1:1 with the Blueprint §3.2 service catalog (ADR-9.3).

Each subpackage exposes a public interface module; cross-module imports of
anything non-public fail the boundary lint.
"""
