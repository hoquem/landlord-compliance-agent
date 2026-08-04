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
