"""Production readiness checks for the existing ReDOCX Compliance workflow.
Run from the project root: python -m backend.check_compliance_ready
No customer document is processed. Approval metadata is an operator attestation.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import tempfile

def check_readiness(*, require_approved: bool = True, probe_scanner: bool = True) -> dict:
    from backend.src.schema import ComplianceJurisdiction, ComplianceSectorPack
    from backend.src.processing.compliance.registry import ComplianceRuleRegistry, RuleRegistryError
    from backend.src.processing.compliance.evidence import bounded_regex
    from backend.src.processing.compliance.renderers import _configure_report_pdf
    from backend.upload_security import scan_with_malware_scanner
    import pytesseract

    previous = os.environ.get("COMPLIANCE_REQUIRE_APPROVED_RULE_PACKS")
    os.environ["COMPLIANCE_REQUIRE_APPROVED_RULE_PACKS"] = "1" if require_approved else "0"
    issues = []
    inventory = []
    if require_approved and str(previous or "").strip().lower() not in {"1", "true", "yes"}:
        issues.append({"code": "APPROVAL_ENFORCEMENT_DISABLED"})
    try:
        if bounded_regex is None:
            issues.append({"code": "BOUNDED_MATCHER_UNAVAILABLE"})
        try:
            _configure_report_pdf()
        except Exception as exc:
            issues.append({"code": "PDF_RENDERER_UNAVAILABLE", "detail": str(exc)})
        try:
            pytesseract.get_tesseract_version()
            languages = set(pytesseract.get_languages(config=""))
            if not {"eng", "fra"} <= languages:
                issues.append({"code": "OCR_LANGUAGES_MISSING", "required": ["eng", "fra"]})
        except Exception as exc:
            issues.append({"code": "OCR_UNAVAILABLE", "detail": str(exc)})
        if probe_scanner:
            try:
                with tempfile.TemporaryDirectory(prefix="redocx-compliance-readiness-") as directory:
                    probe = Path(directory) / "readiness.txt"
                    probe.write_text("ReDOCX Compliance readiness probe.\n", encoding="utf-8")
                    scan_with_malware_scanner(probe, mode="required")
            except Exception as exc:
                issues.append({"code": "MALWARE_SCANNER_UNAVAILABLE", "detail": str(exc)})
        else:
            issues.append({"code": "MALWARE_SCANNER_NOT_PROBED"})
        registry = ComplianceRuleRegistry()
        for value in registry.list_available_jurisdictions():
            try:
                jurisdiction = ComplianceJurisdiction(value)
            except ValueError:
                continue
            packs = []
            values = set(registry.list_available_sector_packs(jurisdiction=jurisdiction))
            if jurisdiction == ComplianceJurisdiction.nigeria and "nigeria_core_control_library" in values:
                values.add("core_control_library")
                values.discard("nigeria_core_control_library")
            for pack_value in sorted(values):
                try:
                    sector = ComplianceSectorPack(pack_value)
                    loaded = registry.load_pack(jurisdiction=jurisdiction, sector_pack=sector)
                    packs.append({"sector_pack": sector.value, "version": loaded.version,
                                  "rules": len(loaded.rules), "checksum_sha256": loaded.checksum_sha256})
                except (ValueError, RuleRegistryError) as exc:
                    issues.append({"code": "RULE_PACK_INVALID", "jurisdiction": value,
                                   "sector_pack": pack_value, "detail": str(exc)})
            if any(pack["sector_pack"] == "core_control_library" for pack in packs):
                inventory.append({"jurisdiction": value, "packs": packs})
            elif packs:
                issues.append({"code": "REQUIRED_CORE_PACK_MISSING", "jurisdiction": value})
        if not inventory:
            issues.append({"code": "NO_READY_JURISDICTIONS"})
        if not require_approved:
            issues.append({"code": "DEVELOPMENT_RULE_APPROVAL_BYPASS"})
        return {
            "production_ready": not issues,
            "checks_passed": not [issue for issue in issues if issue["code"] != "DEVELOPMENT_RULE_APPROVAL_BYPASS"],
            "approval_metadata_required": require_approved,
            "inventory": inventory,
            "issues": issues,
            "notice": "This checks deployed software and approval provenance; legal substance and applicability require qualified review.",
        }
    finally:
        if previous is None:
            os.environ.pop("COMPLIANCE_REQUIRE_APPROVED_RULE_PACKS", None)
        else:
            os.environ["COMPLIANCE_REQUIRE_APPROVED_RULE_PACKS"] = previous

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", action="store_true", help="Inspect structural readiness without legal-review attestation; never reports production_ready.")
    parser.add_argument("--rules-root", help="Override COMPLIANCE_RULES_ROOT for this command.")
    args = parser.parse_args()
    if args.rules_root:
        os.environ["COMPLIANCE_RULES_ROOT"] = args.rules_root
    result = check_readiness(require_approved=not args.development)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["checks_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())

