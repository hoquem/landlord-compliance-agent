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
