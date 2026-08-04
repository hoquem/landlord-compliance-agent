/// The seam between the app and the backend.
///
/// Abstract for the same reason `AuthSession` is: widget tests must be able
/// to drive every screen without a running API, a database, or a network.
/// `HttpApiClient` is the only implementation that knows about HTTP.
///
/// **Errors are values, not exceptions to swallow.** The backend fails
/// loudly on purpose — the parser names the row that broke, the export names
/// the transactions blocking it — and [ApiException] carries that text
/// through so a screen can show it. Flattening it to "something went wrong"
/// would waste the backend's honesty, which `PRODUCT.md` lists as a design
/// principle rather than a nicety.
library;

import 'dart:typed_data';

import 'models.dart';

/// A backend call that failed, carrying whatever the backend said.
class ApiException implements Exception {
  const ApiException(this.statusCode, this.detail);

  final int statusCode;

  /// The `detail` field of the API's error body, verbatim where possible.
  final String detail;

  @override
  String toString() => detail;
}

abstract class ApiClient {
  /// Every import in the caller's org, oldest first.
  Future<List<ImportSummary>> listImports();

  /// Upload one statement CSV.
  ///
  /// Returns 201 **whether or not parsing succeeded**: a refused file is a
  /// recorded outcome, not a rejected request, so the result may well be an
  /// import with `status == 'failed'` and a row number to show.
  Future<ImportSummary> uploadStatement({
    required String entityId,
    required String sourceBank,
    required String filename,
    required Uint8List bytes,
  });

  /// The caller's entities, for the upload form.
  Future<List<Entity>> listEntities();

  /// Bank names the parser accepts.
  ///
  /// Fetched rather than hard-coded: `core/parser.py`'s registry is the
  /// source of truth, and a copy here would drift into offering a bank the
  /// parser refuses.
  Future<List<String>> listBanks();
}
