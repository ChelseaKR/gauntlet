"""Adapters that put real, separately-built systems behind the target contract.

Nothing under this package is vendored from anywhere. Each adapter talks to a
system that lives elsewhere: a public HTTP endpoint, or a package installed
from its public repository into a virtual environment. The adapter's job is to
translate that system's own response shape into ``TargetResponse`` without
inferring anything the system did not report, and to report the provenance a
committed result pack owes: which version answered, on which model, with which
prompt version, and how many requests the run cost.
"""
