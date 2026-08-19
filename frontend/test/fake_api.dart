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

  DashboardSummary summary = DashboardSummary(
    needsDecision: 0,
    nextDeadline: DateTime.utc(2026, 11, 7),
    daysUntilDeadline: 95,
    expiringCertificates: 0,
    expiredCertificates: 0,
    unreadableImports: 0,
    uncategorisedImports: 0,
  );

  List<PropertyCertificates> certificateGroups = <PropertyCertificates>[];
  List<Map<String, Object?>> createdCertificates = <Map<String, Object?>>[];
  List<String> deletedCertificates = <String>[];

  Object? failExport;
  ExportResult exportResult = const ExportResult(
    taxYear: '2026-27',
    quarter: 'Q1',
    version: 1,
    documents: <ExportDocument>[
      ExportDocument(id: 'd1', kind: 'export_category_csv'),
      ExportDocument(id: 'd2', kind: 'export_pdf'),
    ],
  );
  List<String> downloadedDocuments = <String>[];

  Map<String, List<OwnershipShare>> ownership =
      <String, List<OwnershipShare>>{};
  List<List<OwnershipShare>> savedOwnership = <List<OwnershipShare>>[];
  Object? failSetOwnership;
  List<Map<String, String>> createdEntities = <Map<String, String>>[];
  List<Map<String, String>> createdProperties = <Map<String, String>>[];
  List<Map<String, String?>> updatedProperties = <Map<String, String?>>[];
  List<String> deletedProperties = <String>[];

  @override
  Future<void> createEntity({
    required String name,
    required String taxRegime,
  }) async {
    createdEntities.add(<String, String>{'name': name, 'tax_regime': taxRegime});
  }

  @override
  Future<void> createProperty({
    required String addressLine1,
    String? addressLine2,
    required String city,
    required String postcode,
    required String financeCostClassification,
    String? mortgageType,
  }) async {
    createdProperties.add(<String, String>{'address_line1': addressLine1});
  }

  @override
  Future<void> updateProperty(
    String propertyId, {
    String? mortgageType,
    String? addressLine1,
    String? addressLine2,
    String? city,
    String? postcode,
    String? financeCostClassification,
  }) async {
    updatedProperties.add(<String, String?>{
      'id': propertyId,
      'mortgage_type': mortgageType,
      'address_line1': addressLine1,
      'address_line2': addressLine2,
      'city': city,
      'postcode': postcode,
      'finance_cost_classification': financeCostClassification,
    });
  }

  @override
  Future<void> deleteProperty(String propertyId) async {
    deletedProperties.add(propertyId);
    properties = <PropertyRef>[
      for (final PropertyRef p in properties)
        if (p.id != propertyId) p,
    ];
  }

  @override
  Future<List<OwnershipShare>> getOwnership(String propertyId) async =>
      ownership[propertyId] ?? <OwnershipShare>[];

  @override
  Future<void> setOwnership(
    String propertyId,
    List<OwnershipShare> shares,
  ) async {
    savedOwnership.add(shares);
    if (failSetOwnership != null) throw failSetOwnership!;
    ownership[propertyId] = shares;
  }

  @override
  Future<DashboardSummary> getDashboard() async => summary;

  @override
  Future<List<PropertyCertificates>> listCertificates() async =>
      certificateGroups;

  @override
  Future<void> createCertificate({
    required String propertyId,
    required String certificateType,
    required DateTime expiryDate,
    String? certificateRef,
  }) async {
    createdCertificates.add(<String, Object?>{
      'property_id': propertyId,
      'certificate_type': certificateType,
      'expiry_date': expiryDate,
      'certificate_ref': certificateRef,
    });
  }

  @override
  Future<void> deleteCertificate(String certificateId) async {
    deletedCertificates.add(certificateId);
    certificateGroups = <PropertyCertificates>[
      for (final PropertyCertificates g in certificateGroups)
        PropertyCertificates(
          propertyId: g.propertyId,
          certificates: <Certificate>[
            for (final Certificate c in g.certificates)
              if (c.id != certificateId) c,
          ],
        ),
    ];
  }

  @override
  Future<ExportResult> exportQuarter({
    required String entityId,
    required int taxYear,
    required int quarter,
  }) async {
    if (failExport != null) throw failExport!;
    return exportResult;
  }

  @override
  Future<String> downloadUrl(String documentId) async {
    downloadedDocuments.add(documentId);
    return 'https://example.test/signed/$documentId';
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

/// Build a [Certificate] without repeating every field in each test.
Certificate aCertificate({
  String id = 'c1',
  String propertyId = 'p1',
  String type = 'gas_safety',
  String status = 'valid',
  DateTime? expiry,
}) => Certificate(
  id: id,
  propertyId: propertyId,
  certificateType: type,
  expiryDate: expiry ?? DateTime.utc(2027, 3, 1),
  status: status,
);

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
