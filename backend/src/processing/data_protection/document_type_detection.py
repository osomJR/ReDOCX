from __future__ import annotations

"""Deterministic privacy-document classification for Redaction and Data Masking.

The classifier deliberately avoids sending document text to another service. It works on
text already extracted by ReDOCX's secure upload/OCR pipeline plus the original filename,
returns only a document-type enum and confidence metadata, and always falls back to the
broad ``general_document`` class when the evidence is weak.
"""

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable

from backend.src.schema import RedactionMaskingDocumentType


CLASSIFIER_VERSION = "privacy-document-type-v1"
MAX_CLASSIFICATION_CHARS = 250_000
_MIN_CLASSIFICATION_SCORE = 4.0


@dataclass(frozen=True)
class _Rule:
    document_type: RedactionMaskingDocumentType
    strong_phrases: tuple[str, ...] = ()
    supporting_phrases: tuple[str, ...] = ()
    filename_phrases: tuple[str, ...] = ()
    priority: int = 0


@dataclass(frozen=True)
class DocumentTypeDetectionResult:
    document_type: RedactionMaskingDocumentType
    confidence: float
    is_fallback: bool
    classifier_version: str = CLASSIFIER_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "document_type": self.document_type.value,
            "confidence": round(max(0.0, min(1.0, self.confidence)), 3),
            "is_fallback": self.is_fallback,
            "classifier_version": self.classifier_version,
        }


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    ascii_like = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = ascii_like.lower()
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _phrase_present(haystack: str, phrase: str) -> bool:
    normalized_phrase = _normalize(phrase)
    if not haystack or not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {haystack} "


def _matched_phrases(haystack: str, phrases: Iterable[str]) -> list[str]:
    return [phrase for phrase in phrases if _phrase_present(haystack, phrase)]


def _rules() -> tuple[_Rule, ...]:
    # Strong phrases are intentionally document-structural rather than entity-specific.
    # This keeps the detector stable across jurisdictions and avoids learning from PII.
    return (
        _Rule(
            RedactionMaskingDocumentType.id_document,
            strong_phrases=(
                "national identity card",
                "national identification card",
                "identity card",
                "identity document",
                "passport number",
                "drivers license",
                "driver license",
                "driving licence",
                "residence permit",
            ),
            supporting_phrases=("date of birth", "place of birth", "sex", "gender", "nationality", "surname"),
            filename_phrases=("passport", "identity card", "national id", "driver license", "drivers license"),
            priority=120,
        ),
        _Rule(
            RedactionMaskingDocumentType.medical_record,
            strong_phrases=(
                "medical fitness certificate",
                "medical certificate",
                "medical record",
                "clinical report",
                "laboratory report",
                "discharge summary",
                "patient record",
                "prescription",
            ),
            supporting_phrases=(
                "hospital",
                "patient",
                "diagnosis",
                "examination findings",
                "blood pressure",
                "urinalysis",
                "chest x ray",
                "consultant",
                "physician",
            ),
            filename_phrases=("medical", "clinical", "hospital", "fitness certificate", "lab report"),
            priority=118,
        ),
        _Rule(
            RedactionMaskingDocumentType.academic_record,
            strong_phrases=(
                "academic transcript",
                "student transcript",
                "statement of results",
                "academic record",
                "grade report",
                "semester result",
            ),
            supporting_phrases=("course title", "credit unit", "grade", "gpa", "cgpa", "matric", "semester"),
            filename_phrases=("transcript", "academic record", "statement of result", "grade report"),
            priority=116,
        ),
        _Rule(
            RedactionMaskingDocumentType.academic_certificate,
            strong_phrases=(
                "this is to certify",
                "been admitted to the degree of",
                "degree certificate",
                "academic certificate",
                "certificate of graduation",
                "diploma certificate",
                "bachelor of",
                "master of",
                "doctor of philosophy",
            ),
            supporting_phrases=("university", "college", "degree", "graduation", "registrar", "chancellor"),
            filename_phrases=("degree certificate", "academic certificate", "graduation certificate", "b eng certificate", "bsc certificate", "msc certificate"),
            priority=114,
        ),
        _Rule(
            RedactionMaskingDocumentType.admission_enrollment_document,
            strong_phrases=(
                "provisional letter of admission",
                "offered provisional admission",
                "letter of admission",
                "admission letter",
                "change of course institution",
                "change of institution course",
                "enrollment confirmation",
                "enrolment confirmation",
            ),
            supporting_phrases=("admission", "registration number", "examination number", "institution", "course", "matriculation", "student"),
            filename_phrases=("admission", "change of course", "change of institution", "enrollment", "enrolment"),
            priority=112,
        ),
        _Rule(
            RedactionMaskingDocumentType.kyc_document,
            strong_phrases=("know your customer", "customer due diligence", "enhanced due diligence", "kyc form", "kyc document"),
            supporting_phrases=("beneficial owner", "source of funds", "customer identification", "pep", "sanctions screening"),
            filename_phrases=("kyc", "customer due diligence", "cdd"),
            priority=110,
        ),
        _Rule(
            RedactionMaskingDocumentType.bank_statement,
            strong_phrases=("bank statement", "account statement", "statement period", "transaction history"),
            supporting_phrases=("opening balance", "closing balance", "available balance", "debit", "credit", "transaction date", "account number"),
            filename_phrases=("bank statement", "account statement"),
            priority=108,
        ),
        _Rule(
            RedactionMaskingDocumentType.payroll_document,
            strong_phrases=("pay slip", "payslip", "payroll statement", "salary slip", "earnings statement"),
            supporting_phrases=("gross pay", "net pay", "deductions", "basic salary", "employee id", "pay period"),
            filename_phrases=("payslip", "pay slip", "payroll", "salary slip"),
            priority=106,
        ),
        _Rule(
            RedactionMaskingDocumentType.invoice,
            strong_phrases=("invoice", "invoice number", "tax invoice", "bill to", "amount due"),
            supporting_phrases=("subtotal", "unit price", "quantity", "due date", "billing address", "vat", "total amount"),
            filename_phrases=("invoice", "bill"),
            priority=104,
        ),
        _Rule(
            RedactionMaskingDocumentType.receipt,
            strong_phrases=("receipt", "payment receipt", "official receipt", "receipt number", "payment received"),
            supporting_phrases=("amount paid", "payment method", "cashier", "transaction id", "balance due"),
            filename_phrases=("receipt",),
            priority=103,
        ),
        _Rule(
            RedactionMaskingDocumentType.tax_document,
            strong_phrases=("tax return", "income tax", "tax assessment", "tax certificate", "withholding tax", "vat return"),
            supporting_phrases=("taxpayer", "tax identification", "taxable income", "tax year", "tax authority", "tin"),
            filename_phrases=("tax return", "tax certificate", "tax assessment", "withholding tax"),
            priority=102,
        ),
        _Rule(
            RedactionMaskingDocumentType.insurance_document,
            strong_phrases=("insurance policy", "certificate of insurance", "insurance claim", "claim form", "policy schedule"),
            supporting_phrases=("policy number", "insured", "insurer", "premium", "beneficiary", "coverage", "claim number"),
            filename_phrases=("insurance", "policy schedule", "claim form"),
            priority=101,
        ),
        _Rule(
            RedactionMaskingDocumentType.compliance_regulatory_document,
            strong_phrases=("compliance clearance", "compliance report", "regulatory compliance", "compliance certificate", "compliance assessment"),
            supporting_phrases=("compliance", "complied", "regulatory", "regulation", "policy", "clearance", "issued by"),
            filename_phrases=("compliance", "regulatory", "clearance"),
            priority=100,
        ),
        _Rule(
            RedactionMaskingDocumentType.audit_document,
            strong_phrases=("audit report", "independent auditor", "internal audit", "audit findings", "auditors report"),
            supporting_phrases=("audit", "auditor", "material weakness", "control deficiency", "assurance", "findings"),
            filename_phrases=("audit report", "internal audit", "audit findings"),
            priority=99,
        ),
        _Rule(
            RedactionMaskingDocumentType.financial_statement,
            strong_phrases=("financial statements", "statement of financial position", "balance sheet", "income statement", "cash flow statement", "profit and loss"),
            supporting_phrases=("assets", "liabilities", "equity", "revenue", "expenses", "fiscal year"),
            filename_phrases=("financial statement", "balance sheet", "income statement", "profit and loss", "cash flow"),
            priority=98,
        ),
        _Rule(
            RedactionMaskingDocumentType.immigration_travel_document,
            strong_phrases=("visa application", "visa grant", "immigration", "boarding pass", "travel itinerary", "entry permit", "work permit"),
            supporting_phrases=("passport", "flight", "departure", "arrival", "destination", "visa", "permit number"),
            filename_phrases=("visa", "immigration", "boarding pass", "travel itinerary", "work permit"),
            priority=97,
        ),
        _Rule(
            RedactionMaskingDocumentType.property_real_estate_document,
            strong_phrases=("land title", "certificate of occupancy", "property deed", "deed of assignment", "tenancy agreement", "lease agreement", "real estate"),
            supporting_phrases=("property", "landlord", "tenant", "premises", "plot", "parcel", "rent"),
            filename_phrases=("land title", "property", "deed", "tenancy", "lease"),
            priority=96,
        ),
        _Rule(
            RedactionMaskingDocumentType.contract,
            strong_phrases=("agreement between", "this agreement", "service agreement", "employment agreement", "contract agreement", "terms and conditions"),
            supporting_phrases=("party", "parties", "effective date", "hereby agree", "obligations", "termination", "governing law"),
            filename_phrases=("contract", "agreement"),
            priority=95,
        ),
        _Rule(
            RedactionMaskingDocumentType.legal_document,
            strong_phrases=("court order", "affidavit", "judgment", "legal notice", "statement of claim", "writ of summons", "case number"),
            supporting_phrases=("plaintiff", "defendant", "court", "judge", "counsel", "sworn", "notary", "legal"),
            filename_phrases=("court", "affidavit", "judgment", "legal notice", "lawsuit"),
            priority=94,
        ),
        _Rule(
            RedactionMaskingDocumentType.employment_hr_document,
            strong_phrases=("employment offer", "offer of employment", "employment letter", "employee record", "performance review", "human resources", "disciplinary notice"),
            supporting_phrases=("employee", "employer", "job title", "department", "employment", "leave", "hr"),
            filename_phrases=("employment", "employee record", "performance review", "hr record"),
            priority=93,
        ),
        _Rule(
            RedactionMaskingDocumentType.resume_cv,
            strong_phrases=("curriculum vitae", "professional experience", "work experience", "employment history", "career objective"),
            supporting_phrases=("education", "skills", "experience", "references", "profile", "certifications"),
            filename_phrases=("resume", "curriculum vitae", "cv"),
            priority=92,
        ),
        _Rule(
            RedactionMaskingDocumentType.government_public_record,
            strong_phrases=("official gazette", "public notice", "government notice", "ministry of", "public record", "government certificate"),
            supporting_phrases=("government", "ministry", "department", "agency", "official", "public service"),
            filename_phrases=("gazette", "government notice", "public record"),
            priority=91,
        ),
        _Rule(
            RedactionMaskingDocumentType.business_corporate_document,
            strong_phrases=("certificate of incorporation", "articles of association", "memorandum of association", "board resolution", "shareholder resolution", "company profile"),
            supporting_phrases=("company", "corporation", "director", "shareholder", "board", "registered office", "business"),
            filename_phrases=("incorporation", "board resolution", "company profile", "corporate"),
            priority=90,
        ),
        _Rule(
            RedactionMaskingDocumentType.research_technical_document,
            strong_phrases=("research paper", "technical report", "research report", "thesis", "dissertation", "project report", "research methodology"),
            supporting_phrases=("abstract", "methodology", "literature review", "references", "results", "discussion", "experiment", "technical"),
            filename_phrases=("research", "technical report", "thesis", "dissertation", "project report"),
            priority=89,
        ),
        _Rule(
            RedactionMaskingDocumentType.historical_archival_document,
            strong_phrases=("historical record", "archival record", "archive record", "archival document", "historical manuscript", "census record", "heritage record"),
            supporting_phrases=("archive", "archival", "historical", "manuscript", "chronicle", "heritage", "collection", "catalogue"),
            filename_phrases=("archive", "archival", "historical", "manuscript"),
            priority=88,
        ),
        _Rule(
            RedactionMaskingDocumentType.procurement_document,
            strong_phrases=("purchase order", "request for proposal", "request for quotation", "invitation to tender", "tender document", "procurement"),
            supporting_phrases=("vendor", "supplier", "quotation", "purchase", "rfp", "rfq", "bid"),
            filename_phrases=("purchase order", "procurement", "rfp", "rfq", "tender", "quotation"),
            priority=87,
        ),
        _Rule(
            RedactionMaskingDocumentType.utility_telecom_document,
            strong_phrases=("utility bill", "electricity bill", "water bill", "telephone bill", "mobile bill", "internet bill"),
            supporting_phrases=("meter number", "service address", "account number", "billing period", "units consumed", "tariff", "telecom"),
            filename_phrases=("utility bill", "electricity bill", "water bill", "phone bill", "internet bill"),
            priority=86,
        ),
        _Rule(
            RedactionMaskingDocumentType.policy_procedure_document,
            strong_phrases=("standard operating procedure", "policy document", "procedure manual", "employee handbook", "operating procedure"),
            supporting_phrases=("policy", "procedure", "scope", "responsibilities", "effective date", "version", "approval"),
            filename_phrases=("policy", "procedure", "sop", "handbook", "manual"),
            priority=85,
        ),
        _Rule(
            RedactionMaskingDocumentType.application_form,
            strong_phrases=("application form", "registration form", "request form", "applicant information", "application details"),
            supporting_phrases=("applicant", "complete this form", "please complete", "signature", "date", "form"),
            filename_phrases=("application form", "registration form", "request form"),
            priority=84,
        ),
        _Rule(
            RedactionMaskingDocumentType.correspondence,
            strong_phrases=("business correspondence", "official correspondence", "memorandum", "memo to", "letter to"),
            supporting_phrases=("dear", "sincerely", "to whom it may concern", "subject", "regards", "recipient"),
            filename_phrases=("letter", "memo", "correspondence"),
            priority=70,
        ),
    )


def _score_rule(rule: _Rule, text: str, filename: str) -> tuple[float, int]:
    strong_hits = _matched_phrases(text, rule.strong_phrases)
    supporting_hits = _matched_phrases(text, rule.supporting_phrases)
    filename_hits = _matched_phrases(filename, rule.filename_phrases)

    score = (
        6.0 * min(len(strong_hits), 4)
        + 1.75 * min(len(supporting_hits), 7)
        + 4.0 * min(len(filename_hits), 2)
    )
    return score, len(strong_hits) + len(supporting_hits) + len(filename_hits)


def _confidence(top_score: float, second_score: float, signal_count: int) -> float:
    if top_score <= 0:
        return 0.2
    strength = min(1.0, top_score / 20.0)
    margin_ratio = max(0.0, min(1.0, (top_score - second_score) / max(top_score, 1.0)))
    signal_factor = min(1.0, signal_count / 5.0)
    return min(0.99, 0.48 + 0.30 * strength + 0.17 * margin_ratio + 0.05 * signal_factor)


def detect_privacy_document_type(
    *,
    text: str | None,
    filename: str | None = None,
) -> DocumentTypeDetectionResult:
    """Classify one privacy-processing document with a conservative fallback.

    ``text`` should be the server-extracted/OCR text already produced by ReDOCX.
    The original filename is used only as secondary evidence. The function never
    returns document contents or matched PII.
    """

    normalized_text = _normalize((text or "")[:MAX_CLASSIFICATION_CHARS])
    normalized_filename = _normalize(filename)

    scored: list[tuple[float, int, int, RedactionMaskingDocumentType]] = []
    for rule in _rules():
        score, signal_count = _score_rule(rule, normalized_text, normalized_filename)
        scored.append((score, signal_count, rule.priority, rule.document_type))

    scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    top_score, top_signal_count, _priority, top_type = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0

    if top_score < _MIN_CLASSIFICATION_SCORE:
        return DocumentTypeDetectionResult(
            document_type=RedactionMaskingDocumentType.general_document,
            confidence=_confidence(top_score, second_score, top_signal_count),
            is_fallback=True,
        )

    return DocumentTypeDetectionResult(
        document_type=top_type,
        confidence=_confidence(top_score, second_score, top_signal_count),
        is_fallback=False,
    )


__all__ = [
    "CLASSIFIER_VERSION",
    "DocumentTypeDetectionResult",
    "detect_privacy_document_type",
]
