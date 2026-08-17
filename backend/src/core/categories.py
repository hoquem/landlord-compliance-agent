"""HMRC category enum for MTD ITSA property submissions.

:seealso: spec §Data model; SA105 mapping validated 2026-07-28.
"""

from enum import StrEnum


class HmrcCategory(StrEnum):
    """HMRC SA105 property-income/expense categories plus VAT handling.

    Single source of truth for this project. Mirrors the SQL-side
    ``hmrc_category`` enum created in ``supabase/migrations/0001_core.sql``
    exactly -- ``backend/tests/db/test_schema.py`` guards the SQL side
    against drift, ``backend/tests/db/test_models_roundtrip.py`` guards the
    SQLAlchemy column mapping against drift from this enum.

    VAT categories (vat_output, vat_input) are not SA105 categories -- they
    are liability movements that must not appear in the P&L or quarterly
    export. They exist so VAT payments/refunds can be categorised rather
    than left as unclassified noise, and so the export pipeline can drop
    them alongside capital_expense and personal_non_business.
    """

    RENT_INCOME = "rent_income"
    OTHER_PROPERTY_INCOME = "other_property_income"
    RENT_PAID = "rent_paid"
    RATES_INSURANCE_GROUND = "rates_insurance_ground"
    REPAIRS_MAINTENANCE = "repairs_maintenance"
    FINANCE_COSTS_RESIDENTIAL = "finance_costs_residential"
    FINANCE_COSTS_NONRESIDENTIAL = "finance_costs_nonresidential"
    LEGAL_PROFESSIONAL = "legal_professional"
    SERVICE_COSTS = "service_costs"
    TRAVEL_VEHICLE = "travel_vehicle"
    OTHER_ALLOWABLE = "other_allowable"
    REPLACEMENT_DOMESTIC_ITEMS = "replacement_domestic_items"
    USE_OF_HOME_ALLOWANCE = "use_of_home_allowance"
    CAPITAL_EXPENSE = "capital_expense"
    PERSONAL_NON_BUSINESS = "personal_non_business"
    NON_DEDUCTIBLE_BUSINESS = "non_deductible_business"
    VAT_OUTPUT = "vat_output"
    VAT_INPUT = "vat_input"


INCOME_CATEGORIES = {HmrcCategory.RENT_INCOME, HmrcCategory.OTHER_PROPERTY_INCOME}
EXCLUDED_FROM_EXPORT = {
    HmrcCategory.CAPITAL_EXPENSE,
    HmrcCategory.PERSONAL_NON_BUSINESS,
    HmrcCategory.NON_DEDUCTIBLE_BUSINESS,
    HmrcCategory.VAT_OUTPUT,
    HmrcCategory.VAT_INPUT,
}
