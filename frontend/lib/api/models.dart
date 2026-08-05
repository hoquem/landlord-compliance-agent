/// Wire models for the backend API.
///
/// Deliberately hand-written rather than code-generated: the API surface is
/// small, and a generator would add a build step and a lockstep dependency
/// for four classes.
///
/// **Names come from the glossaries** (`docs/domain/money.md`,
/// `docs/domain/compliance.md`), not from whatever reads nicely in Dart. A
/// field called `category` here and `hmrc_category` there is how two halves
/// of one system start meaning different things.
library;

/// The five states every status in this product collapses into.
///
/// The database has three separate vocabularies — transactions
/// (`unclassified/proposed/confirmed/excluded`), certificates
/// (`valid/expiring/expired`) and imports
/// (`pending/parsed/failed/categorisation_failed`). The interface has one,
/// learned once and true everywhere. See `DESIGN.md`.
enum WorkState {
  /// The machine is busy and you cannot act yet.
  working,

  /// Waiting on a human decision.
  needsYou,

  /// Decided. Recedes to neutral, so a screen calms as work is done.
  settled,

  /// Broken, and someone has to look.
  wrong,

  /// Deliberately excluded. Dimmest.
  setAside,
}

/// One statement import.
class ImportSummary {
  const ImportSummary({
    required this.id,
    required this.sourceBank,
    required this.status,
    required this.createdAt,
    this.errorDetail,
    this.entityId,
  });

  factory ImportSummary.fromJson(Map<String, dynamic> json) {
    return ImportSummary(
      id: json['id'] as String,
      sourceBank: json['source_bank'] as String,
      status: json['status'] as String,
      createdAt: DateTime.parse(json['created_at'] as String),
      errorDetail: json['error_detail'] as Map<String, dynamic>?,
      entityId: json['entity_id'] as String?,
    );
  }

  final String id;
  final String sourceBank;
  final String status;
  final DateTime createdAt;

  /// `{row_number, message}` when parsing failed. The row number is the
  /// whole point: the backend goes to the trouble of naming the line that
  /// broke, and flattening that to "import failed" throws it away.
  final Map<String, dynamic>? errorDetail;

  final String? entityId;

  WorkState get state => switch (status) {
    'pending' => WorkState.working,
    'parsed' => WorkState.settled,
    'failed' || 'categorisation_failed' => WorkState.wrong,
    _ => WorkState.working,
  };

  /// The status word shown to a human, in the domain's own language.
  ///
  /// Status is never colour alone — every state is paired with this.
  String get label => switch (status) {
    'pending' => 'Reading',
    'parsed' => 'Imported',
    'failed' => 'Failed',
    'categorisation_failed' => 'Categorising failed',
    _ => status,
  };

  /// The failing row number, if a parse failure named one.
  int? get failedRow => errorDetail?['row_number'] as int?;

  /// The failure message, if there was one.
  String? get failureMessage => errorDetail?['message'] as String?;
}

/// One of the caller's entities.
class Entity {
  const Entity({required this.id, required this.name, required this.taxRegime});

  factory Entity.fromJson(Map<String, dynamic> json) => Entity(
    id: json['id'] as String,
    name: json['name'] as String,
    taxRegime: json['tax_regime'] as String,
  );

  final String id;
  final String name;
  final String taxRegime;
}

/// One property, as the review screen needs it.
class PropertyRef {
  const PropertyRef({required this.id, required this.label});

  factory PropertyRef.fromJson(Map<String, dynamic> json) => PropertyRef(
    id: json['id'] as String,
    label: <String?>[
      json['address_line1'] as String?,
      json['postcode'] as String?,
    ].whereType<String>().join(', '),
  );

  final String id;
  final String label;
}

/// One transaction, at whatever stage of review it has reached.
class Txn {
  const Txn({
    required this.id,
    required this.date,
    required this.amount,
    required this.direction,
    required this.description,
    required this.status,
    this.hmrcCategory,
    this.propertyId,
    this.confidence,
  });

  factory Txn.fromJson(Map<String, dynamic> json) => Txn(
    id: json['id'] as String,
    date: DateTime.parse(json['date'] as String),
    amount: double.parse('${json['amount']}'),
    direction: json['direction'] as String,
    description: json['description'] as String,
    status: json['status'] as String,
    hmrcCategory: json['hmrc_category'] as String?,
    propertyId: json['property_id'] as String?,
    confidence: json['confidence'] == null
        ? null
        : double.parse('${json['confidence']}'),
  );

  final String id;
  final DateTime date;

  /// A **magnitude**, always positive. The sign lives in [direction],
  /// exactly as the column pair does — see `docs/domain/money.md`. Anything
  /// that needs a signed number derives it; nothing stores one.
  final double amount;

  final String direction;
  final String description;
  final String status;
  final String? hmrcCategory;
  final String? propertyId;

  /// The agent's confidence, 0–1, or null if no agent has seen this line.
  final double? confidence;

  /// Signed for display only: money out reads negative.
  ///
  /// This is the *parser's* convention (direction alone), not the export's
  /// (which also depends on category). A line being reviewed has no settled
  /// category yet, so direction is all there is.
  double get signedForDisplay => direction == 'out' ? -amount : amount;

  WorkState get state => switch (status) {
    'unclassified' || 'proposed' => WorkState.needsYou,
    'confirmed' => WorkState.settled,
    'excluded' => WorkState.setAside,
    _ => WorkState.working,
  };

  String get label => switch (status) {
    'unclassified' => 'Not categorised',
    'proposed' => 'Proposed',
    'confirmed' => 'Confirmed',
    'excluded' => 'Excluded',
    _ => status,
  };

  /// Whether a human still has to decide about this line.
  ///
  /// `proposed` counts: an agent's suggestion nobody accepted is not a
  /// decision, and `export_pack.py` refuses to export while one remains.
  bool get needsDecision => status == 'unclassified' || status == 'proposed';
}

/// Below this, a proposal is shown as unsettled and sorted to the top.
///
/// Confidence changes **what you see first**, never what appears true. It
/// is deliberately not a colour: a low-confidence proposal is not an error,
/// it is "look here first", and tinting it would collide with the status
/// vocabulary and imply the agent erred by being unsure.
const double kLowConfidence = 0.8;

/// What is waiting on the user, from `GET /dashboard`.
///
/// The deadline is **computed by the backend** and only rendered here.
/// `core/quarters.py` owns the four statutory dates and has unit tests
/// pinning all of them; a second implementation in Dart would be a second
/// opinion about a tax deadline, and the failure mode is filing late.
class DashboardSummary {
  const DashboardSummary({
    required this.needsDecision,
    required this.nextDeadline,
    required this.daysUntilDeadline,
    required this.expiringCertificates,
    required this.expiredCertificates,
    required this.unreadableImports,
    required this.uncategorisedImports,
  });

  factory DashboardSummary.fromJson(Map<String, dynamic> json) =>
      DashboardSummary(
        needsDecision: json['needs_decision'] as int,
        nextDeadline: DateTime.parse(json['next_deadline'] as String),
        daysUntilDeadline: json['days_until_deadline'] as int,
        expiringCertificates: json['expiring_certificates'] as int,
        expiredCertificates: json['expired_certificates'] as int,
        unreadableImports: json['unreadable_imports'] as int,
        uncategorisedImports: json['uncategorised_imports'] as int,
      );

  final int needsDecision;
  final DateTime nextDeadline;
  final int daysUntilDeadline;
  final int expiringCertificates;
  final int expiredCertificates;
  /// Files the parser refused -- bad input, fixed by a different export.
  final int unreadableImports;

  /// Files that parsed cleanly and whose categorisation then failed.
  /// Deliberately separate: the data is fine and sitting there.
  final int uncategorisedImports;

  /// Whether anything at all is outstanding.
  bool get allClear =>
      needsDecision == 0 &&
      expiringCertificates == 0 &&
      expiredCertificates == 0 &&
      unreadableImports == 0 &&
      uncategorisedImports == 0;
}

/// One compliance certificate, with the status the API derived for it.
class Certificate {
  const Certificate({
    required this.id,
    required this.propertyId,
    required this.certificateType,
    required this.expiryDate,
    required this.status,
    this.issueDate,
    this.certificateRef,
  });

  factory Certificate.fromJson(Map<String, dynamic> json) => Certificate(
    id: json['id'] as String,
    propertyId: json['property_id'] as String,
    certificateType: json['certificate_type'] as String,
    expiryDate: DateTime.parse(json['expiry_date'] as String),
    status: json['status'] as String,
    issueDate: json['issue_date'] == null
        ? null
        : DateTime.parse(json['issue_date'] as String),
    certificateRef: json['certificate_ref'] as String?,
  );

  final String id;
  final String propertyId;
  final String certificateType;
  final DateTime expiryDate;

  /// `expired`, `expiring` or `valid` — derived by the API on every read,
  /// never stored. A cached flag goes stale exactly when it matters.
  final String status;

  final DateTime? issueDate;
  final String? certificateRef;

  WorkState get state => switch (status) {
    'expired' => WorkState.wrong,
    'expiring' => WorkState.needsYou,
    'valid' => WorkState.settled,
    _ => WorkState.working,
  };

  String get label => switch (status) {
    'expired' => 'Expired',
    'expiring' => 'Expiring',
    'valid' => 'Valid',
    _ => status,
  };
}

/// One property's certificates, as the grouped list returns them.
class PropertyCertificates {
  const PropertyCertificates({
    required this.propertyId,
    required this.certificates,
  });

  factory PropertyCertificates.fromJson(Map<String, dynamic> json) =>
      PropertyCertificates(
        propertyId: json['property_id'] as String,
        certificates: <Certificate>[
          for (final Object? c in json['certificates'] as List<Object?>)
            Certificate.fromJson(c! as Map<String, dynamic>),
        ],
      );

  final String propertyId;
  final List<Certificate> certificates;
}

/// The outcome of one quarterly export.
class ExportResult {
  const ExportResult({
    required this.taxYear,
    required this.quarter,
    required this.documents,
    this.version,
  });

  factory ExportResult.fromJson(Map<String, dynamic> json) => ExportResult(
    taxYear: json['tax_year'] as String,
    quarter: json['quarter'] as String,
    version: json['version'] as int?,
    documents: <ExportDocument>[
      for (final Object? d in json['documents'] as List<Object?>)
        ExportDocument.fromJson(d! as Map<String, dynamic>),
    ],
  );

  final String taxYear;
  final String quarter;

  /// Null for a company: outside MTD ITSA, so nothing is filed.
  final int? version;

  final List<ExportDocument> documents;
}

class ExportDocument {
  const ExportDocument({required this.id, required this.kind});

  factory ExportDocument.fromJson(Map<String, dynamic> json) =>
      ExportDocument(id: json['id'] as String, kind: json['kind'] as String);

  final String id;
  final String kind;

  String get label => switch (kind) {
    'export_category_csv' => 'Return figures (CSV)',
    'export_property_csv' => 'Per-property detail (CSV)',
    'export_pdf' => 'Summary (PDF)',
    _ => kind,
  };
}

/// One entity's share of one property.
class OwnershipShare {
  const OwnershipShare({required this.entityId, required this.percentage});

  factory OwnershipShare.fromJson(Map<String, dynamic> json) => OwnershipShare(
    entityId: json['entity_id'] as String,
    percentage: double.parse('${json['percentage']}'),
  );

  final String entityId;
  final double percentage;
}
