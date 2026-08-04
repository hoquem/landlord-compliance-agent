/// A scriptable [ApiClient] for widget tests.
///
/// Shared across screen suites so there is one fake, not one per file. A
/// second fake is how two tests come to disagree about what the API does.
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:landlord_compliance/api/api_client.dart';
import 'package:landlord_compliance/api/models.dart';

class FakeApiClient implements ApiClient {
  FakeApiClient({
    List<ImportSummary>? imports,
    List<Entity>? entities,
    List<String>? banks,
  }) : imports = imports ?? <ImportSummary>[],
       entities = entities ?? <Entity>[],
       banks = banks ?? <String>['generic', 'barclays'];

  List<ImportSummary> imports;
  List<Entity> entities;
  List<String> banks;

  /// Thrown by the next call to whichever method it names.
  Object? failListImports;
  Object? failUpload;

  int listImportsCalls = 0;
  int uploadCalls = 0;
  Map<String, String>? lastUpload;

  @override
  Future<List<ImportSummary>> listImports() async {
    listImportsCalls++;
    if (failListImports != null) throw failListImports!;
    return imports;
  }

  @override
  Future<List<Entity>> listEntities() async => entities;

  List<Txn> txns = <Txn>[];
  List<PropertyRef> properties = <PropertyRef>[];
  List<String> categories = <String>[
    'rent_income',
    'repairs_maintenance',
    'finance_costs_residential',
    'use_of_home_allowance',
  ];

  Object? failConfirm;

  /// Every batch the screen sent. The *count* is what matters: a screen that
  /// confirms row by row would show the same result and hammer the API.
  List<List<ConfirmItem>> confirmBatches = <List<ConfirmItem>>[];

  int excludeCalls = 0;

  @override
  Future<List<Txn>> listTransactions({String? importId, String? status}) async =>
      txns;

  @override
  Future<List<PropertyRef>> listProperties() async => properties;

  @override
  Future<List<String>> listCategories() async => categories;

  @override
  Future<void> confirmBatch(List<ConfirmItem> items) async {
    confirmBatches.add(items);
    if (failConfirm != null) throw failConfirm!;
    txns = <Txn>[
      for (final Txn t in txns)
        if (items.any((ConfirmItem i) => i.transactionId == t.id))
          Txn(
            id: t.id,
            date: t.date,
            amount: t.amount,
            direction: t.direction,
            description: t.description,
            status: 'confirmed',
            hmrcCategory: items
                .firstWhere((ConfirmItem i) => i.transactionId == t.id)
                .hmrcCategory,
            propertyId: t.propertyId,
            confidence: t.confidence,
          )
        else
          t,
    ];
  }

  @override
  Future<void> excludeTransaction(String transactionId) async {
    excludeCalls++;
  }

  @override
  Future<List<String>> listBanks() async => banks;

  @override
  Future<ImportSummary> uploadStatement({
    required String entityId,
    required String sourceBank,
    required String filename,
    required Uint8List bytes,
  }) async {
    uploadCalls++;
    lastUpload = <String, String>{
      'entity_id': entityId,
      'source_bank': sourceBank,
      'filename': filename,
    };
    if (failUpload != null) throw failUpload!;
    final ImportSummary created = ImportSummary(
      id: 'i${imports.length + 1}',
      sourceBank: sourceBank,
      status: 'parsed',
      createdAt: DateTime.utc(2026, 8, 4),
    );
    imports = <ImportSummary>[...imports, created];
    return created;
  }
}

/// Build a [Txn] without repeating every field in each test.
Txn aTxn({
  String id = 't1',
  String description = 'SAMPLE ESTATES L 98A SAMPLE ROAD BGC',
  double amount = 950.00,
  String direction = 'in',
  String status = 'proposed',
  String? category = 'rent_income',
  String? propertyId,
  double? confidence = 0.95,
  DateTime? date,
}) => Txn(
  id: id,
  date: date ?? DateTime.utc(2026, 7, 20),
  amount: amount,
  direction: direction,
  description: description,
  status: status,
  hmrcCategory: category,
  propertyId: propertyId,
  confidence: confidence,
);

/// Build an [ImportSummary] without repeating every field in each test.
ImportSummary anImport({
  String id = 'i1',
  String sourceBank = 'barclays',
  String status = 'parsed',
  Map<String, dynamic>? errorDetail,
}) => ImportSummary(
  id: id,
  sourceBank: sourceBank,
  status: status,
  createdAt: DateTime.utc(2026, 7, 20),
  errorDetail: errorDetail,
);
