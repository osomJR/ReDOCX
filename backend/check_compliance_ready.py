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
    from backend.src.processing.compliance.registry import ComplianceRuleRegistry
    from backend.src.processing.compliance.evidence import bounded_regex
    from backend.src.processing.compliance.renderers import _configure_report_pdf
    from backend.upload_security import scan_with_malware_scanner
    import pytesseract

    registry = ComplianceRuleRegistry(require_approved=require_approved)
    issues = []
    if require_approved and not ComplianceRuleRegistry().approval_required:
        issues.append({"code": "APPROVAL_ENFORCEMENT_DISABLED"})
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
    catalog = registry.inspect_catalog()
    issues.extend(catalog.issues)
    if bounded_regex is None and not any(issue["code"] == "BOUNDED_MATCHER_UNAVAILABLE" for issue in issues):
        issues.append({"code": "BOUNDED_MATCHER_UNAVAILABLE"})
    inventory = [
        {
            "jurisdiction": country["value"],
            "packs": [
                {
                    "sector_pack": pack["value"],
                    "version": pack["latest_version"],
                    "rules": pack["rule_count"],
                    "checksum_sha256": pack["checksum_sha256"],
                }
                for pack in country["sector_packs"]
            ],
        }
        for country in catalog.jurisdictions
    ]
    if not inventory:
        issues.append({"code": "NO_READY_JURISDICTIONS"})
    if not require_approved:
        issues.append({"code": "DEVELOPMENT_RULE_APPROVAL_BYPASS"})
    return {
        "production_ready": not issues,
        "checks_passed": not [issue for issue in issues if issue["code"] != "DEVELOPMENT_RULE_APPROVAL_BYPASS"],
        "approval_metadata_required": require_approved,
        "rules_root": str(registry.rules_root.resolve()),
        "inventory": inventory,
        "issues": issues,
        "notice": "This checks deployed software and approval provenance; legal substance and applicability require qualified review.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", action="store_true", help="Inspect structural readiness without legal-review attestation; never reports production_ready.")
    parser.add_argument("--rules-root", help="Override COMPLIANCE_RULES_ROOT for this command.")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if args.rules_root:
        os.environ["COMPLIANCE_RULES_ROOT"] = args.rules_root
    result = check_readiness(require_approved=not args.development)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["checks_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())

