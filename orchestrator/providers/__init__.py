"""Phone providers: where a new phone instance comes from.

Today the orchestrator starts phones itself (`redroid-up.sh` containers, or
`deploy-from-golden.sh` VMs). A provider here is an alternative selected with
`ORCH_PHONE_PROVIDER`; unset, nothing in this package is imported.
"""
