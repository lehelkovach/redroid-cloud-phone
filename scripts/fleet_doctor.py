#!/usr/bin/env python3
"""
Fleet doctor: why can't OSLO get a phone?

Reads the lab the way an operator would — OCI instance states and addresses,
the subnet's security-list rules for the ports the lab needs, and TCP probes
from wherever this runs — and prints one diagnosis per finding with the fix.

    python3 scripts/fleet_doctor.py                 # live, needs `oci` on PATH + ~/.oci/config
    python3 scripts/fleet_doctor.py --json out.json # also keep the raw facts
    python3 scripts/fleet_doctor.py --facts f.json  # evaluate saved facts (tests, offline)

The evaluation is pure (`diagnose(facts)`), so it is unit-tested without a cloud.
Exit 0 when the lab can serve a lease, 1 otherwise. Never prints a token.
"""
import argparse
import json
import os
import socket
import subprocess
import sys

LAB = {
    "phone": {
        "name": "cloud-phone-agent-6c58",
        "ocid": "ocid1.instance.oc1.phx.anyhqljrgmifkaqcb7eowcjmmsqe7g53jddy4zzj2nv3p64tybe7nieptvtq",
        "ports": {"ssh": 22, "control_api": 8080, "adb": 5555},
    },
    "orchestrator": {
        "name": "cloud-phone-orch-6c58",
        "ocid": "ocid1.instance.oc1.phx.anyhqljrgmifkaqcy3ghouekg5lqvbsqvay22eq4g5cqgkcqjdti2z3dbwoa",
        "ports": {"ssh": 22, "orchestrator": 8090},
    },
}
NEEDED_INGRESS = {8080: "control API", 8090: "orchestrator", 5555: "adb", 22: "ssh"}


def oci(*args):
    out = subprocess.run(["oci", *args, "--output", "json"], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "oci failed")
    return json.loads(out.stdout)["data"] if out.stdout.strip() else None


def tcp_open(host, port, timeout=6):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def collect(probe=True):
    """Facts from OCI and the network. Each VM: state, ips, subnet; the subnet's ingress rules; port probes."""
    facts = {"vms": {}, "ingress": [], "probes": {}, "from": socket.gethostname()}
    subnet_id = None
    for role, spec in LAB.items():
        inst = oci("compute", "instance", "get", "--instance-id", spec["ocid"])
        vnics = oci("compute", "instance", "list-vnics", "--instance-id", spec["ocid"]) or []
        v = vnics[0] if vnics else {}
        facts["vms"][role] = {
            "name": inst.get("display-name"),
            "state": inst.get("lifecycle-state"),
            "public_ip": v.get("public-ip"),
            "private_ip": v.get("private-ip"),
            "subnet": v.get("subnet-id"),
            "run_command_plugin": any(p.get("name") == "Compute Instance Run Command" and p.get("desired-state") == "ENABLED" for p in ((inst.get("agent-config") or {}).get("plugins-config") or [])),
        }
        subnet_id = subnet_id or v.get("subnet-id")
    if subnet_id:
        sub = oci("network", "subnet", "get", "--subnet-id", subnet_id)
        for slid in sub.get("security-list-ids", []):
            sl = oci("network", "security-list", "get", "--security-list-id", slid)
            for r in sl.get("ingress-security-rules", []):
                dr = ((r.get("tcp-options") or {}).get("destination-port-range")) or {}
                facts["ingress"].append({"source": r.get("source"), "protocol": r.get("protocol"), "min": dr.get("min"), "max": dr.get("max"), "list": sl.get("display-name")})
    if probe:
        for role, spec in LAB.items():
            ip = facts["vms"][role].get("public_ip")
            for label, port in spec["ports"].items():
                facts["probes"][f"{role}:{label}"] = tcp_open(ip, port) if ip else None
    return facts


def ingress_allows(rules, port, source_cidr="0.0.0.0/0"):
    """Does any TCP ingress rule cover `port` for callers in `source_cidr` (any rule whose source contains it)?"""
    for r in rules:
        if str(r.get("protocol")) not in ("6", "all"):
            continue
        lo, hi = r.get("min"), r.get("max")
        if lo is not None and not (lo <= port <= hi):
            continue
        if r.get("source") in ("0.0.0.0/0", source_cidr):
            return True
        if source_cidr != "0.0.0.0/0" and _cidr_contains(r.get("source"), source_cidr):
            return True
    return False


def _cidr_contains(outer, inner):
    try:
        import ipaddress
        return ipaddress.ip_network(inner, strict=False).subnet_of(ipaddress.ip_network(outer, strict=False))
    except Exception:
        return False


def diagnose(facts):
    """One finding per thing that stops a lease, each with the fix. Pure."""
    findings = []
    vms = facts.get("vms", {})
    for role, vm in vms.items():
        if vm.get("state") != "RUNNING":
            findings.append({
                "severity": "blocker", "where": role,
                "what": f"{vm.get('name') or role} is {vm.get('state')} at the OCI level — nothing can answer on it",
                "fix": f"oci compute instance action --action START --instance-id {LAB[role]['ocid']} (and find out who stopped it: oci audit event list)",
            })
    rules = facts.get("ingress", [])
    for port, label in NEEDED_INGRESS.items():
        if port == 8090:
            if not ingress_allows(rules, port, "10.0.0.0/16"):
                findings.append({"severity": "blocker", "where": "network", "what": f"no security-list ingress for :{port} ({label}) from the VCN — OSLO prod (10.0.1.151) cannot reach the orchestrator", "fix": "add TCP 8090 from 10.0.0.0/16 (and from 0.0.0.0/0 only if ORCH_API_TOKEN is set) to the subnet's security list"})
        elif not ingress_allows(rules, port):
            findings.append({"severity": "warning", "where": "network", "what": f"no public ingress for :{port} ({label})", "fix": f"add TCP {port} to the subnet's security list if callers outside the VCN need it"})
    probes = facts.get("probes", {})
    for key, ok in probes.items():
        role, label = key.split(":", 1)
        vm = vms.get(role, {})
        if ok is None or vm.get("state") != "RUNNING":
            continue
        if not ok and label == "ssh":
            findings.append({"severity": "blocker", "where": role, "what": f"{vm.get('name')} :22 refused while RUNNING — the guest is not booted or its firewall drops everything", "fix": "open the OCI console serial connection; check cloud-init and iptables"})
        elif not ok and label in ("control_api", "orchestrator", "adb"):
            expected = {"control_api": "control-api-redroid.service", "orchestrator": "cloud-phone-orchestrator.service", "adb": "redroid-container.service (adb forward 5555)"}[label]
            note = " (:8090 from outside the VCN also needs the security-list rule)" if label == "orchestrator" and not ingress_allows(rules, 8090) else ""
            findings.append({"severity": "blocker", "where": role, "what": f"{vm.get('name')} :{LAB[role]['ports'][label]} ({label}) refused while :22 answers — the service is not listening or the host firewall drops it{note}", "fix": f"on the host: systemctl enable --now {expected}; iptables -I INPUT -p tcp --dport {LAB[role]['ports'][label]} -j ACCEPT; then curl 127.0.0.1:{LAB[role]['ports'][label]}/health"})
    for role, vm in vms.items():
        if vm.get("state") == "RUNNING" and not vm.get("run_command_plugin"):
            findings.append({"severity": "info", "where": role, "what": f"{vm.get('name')} has the Run Command plugin off — an agent cannot repair it without SSH", "fix": "enable 'Compute Instance Run Command' in the instance's Oracle Cloud Agent settings, and grant the dynamic group instance-agent-command-execution-family"})
    ok = not any(f["severity"] == "blocker" for f in findings)
    return {"ok": ok, "findings": findings}


def render(facts, report):
    lines = [f"fleet doctor — probes from {facts.get('from')}"]
    for role, vm in facts.get("vms", {}).items():
        lines.append(f"  {role:13s} {vm.get('name')}: {vm.get('state')} public={vm.get('public_ip')} private={vm.get('private_ip')} runcmd={'on' if vm.get('run_command_plugin') else 'off'}")
    for key, ok in facts.get("probes", {}).items():
        lines.append(f"  probe {key:26s} {'open' if ok else 'refused' if ok is False else 'skipped'}")
    lines.append(f"  ingress tcp ports: {sorted({r.get('min') for r in facts.get('ingress', []) if r.get('min')})}")
    if not report["findings"]:
        lines.append("VERDICT: the lab can serve a lease")
    for f in report["findings"]:
        lines.append(f"{f['severity'].upper():8s} [{f['where']}] {f['what']}\n         fix: {f['fix']}")
    lines.append("VERDICT: " + ("ok" if report["ok"] else "blocked"))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--facts", help="evaluate saved facts instead of calling OCI")
    ap.add_argument("--json", help="write the facts and the report here")
    ap.add_argument("--no-probe", action="store_true", help="skip TCP probes")
    a = ap.parse_args(argv)
    facts = json.load(open(a.facts)) if a.facts else collect(probe=not a.no_probe)
    report = diagnose(facts)
    print(render(facts, report))
    if a.json:
        json.dump({"facts": facts, "report": report}, open(a.json, "w"), indent=2)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
