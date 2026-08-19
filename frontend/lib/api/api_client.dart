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

/// One line of a batch confirm.
class ConfirmItem {
  const ConfirmItem({
    required this.transactionId,
    required this.hmrcCategory,
    this.propertyId,
    this.allowableAmount,
  });

  final String transactionId;
  final String hmrcCategory;

  /// Optional: not every allowable cost belongs to one property.
  /// `use_of_home_allowance` is the standing example.
  final String? propertyId;

  /// The part of the payment that is actually allowable, as a decimal string.
  ///
  /// Sent for a repayment mortgage, where the direct debit is part interest
  /// and part capital and only the interest is deductible. `null` means the
  /// whole amount, which is every ordinary line. Kept as a string so it
  /// reaches the API at the precision the user typed — a double would
  /// introduce binary rounding into a tax figure.
  final String? allowableAmount;

  Map<String, dynamic> toJson() => <String, dynamic>{
    'transaction_id': transactionId,
    'hmrc_category': hmrcCategory,
    'property_id': propertyId,
    if (allowableAmount != null) 'allowable_amount': allowableAmount,
  };
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

  /// Transactions, optionally narrowed to one import or one status.
  Future<List<Txn>> listTransactions({String? importId, String? status});

  /// The caller's properties, for attributing a line.
  Future<List<PropertyRef>> listProperties();

  /// The fifteen HMRC categories, in declaration order.
  ///
  /// Fetched for the same reason as [listBanks]: `core/categories.py` is the
  /// source of truth, the SQL enum is already pinned against it, and a copy
  /// in Dart would be a third opinion.
  Future<List<String>> listCategories();

  /// Confirm many transactions at once.
  ///
  /// One call, not N. The backend applies the whole batch in a single
  /// transaction and abandons all of it on any failure, because a partly
  /// applied batch leaves the user believing they confirmed a screenful
  /// when some of it silently did not take.
  Future<void> confirmBatch(List<ConfirmItem> items);

  /// Mark one transaction as outside the property business.
  Future<void> excludeTransaction(String transactionId);

  /// What is waiting on the user, including the next statutory deadline.
  Future<DashboardSummary> getDashboard();

  /// Certificates, grouped by property.
  Future<List<PropertyCertificates>> listCertificates();

  /// Record one certificate.
  Future<void> createCertificate({
    required String propertyId,
    required String certificateType,
    required DateTime expiryDate,
    String? certificateRef,
  });

  /// Remove one certificate. A superseded or mis-entered one has no value
  /// to keep; what it was survives in the audit row.
  Future<void> deleteCertificate(String certificateId);

  /// Generate a quarter's export pack.
  Future<ExportResult> exportQuarter({
    required String entityId,
    required int taxYear,
    required int quarter,
  });

  /// A short-lived URL for one generated document.
  Future<String> downloadUrl(String documentId);

  /// Create an entity.
  Future<void> createEntity({required String name, required String taxRegime});

  /// Create a property.
  Future<void> createProperty({
    required String addressLine1,
    String? addressLine2,
    required String city,
    required String postcode,
    required String financeCostClassification,
    String? mortgageType,
  });

  /// One property's current ownership set.
  Future<List<OwnershipShare>> getOwnership(String propertyId);

  /// Replace one property's ownership set.
  ///
  /// The **complete** set, not a delta: the API validates that it sums to
  /// exactly 100, and a partial update could not be checked against that
  /// rule without reading the rest first.
  Future<void> setOwnership(String propertyId, List<OwnershipShare> shares);

  /// Update a property's fields (address, mortgage_type, etc).
  ///
  /// Only the named, non-null fields are sent; the backend PATCHes whatever
  /// is present and leaves the rest alone.
  Future<void> updateProperty(
    String propertyId, {
    String? mortgageType,
    String? addressLine1,
    String? addressLine2,
    String? city,
    String? postcode,
    String? financeCostClassification,
  });

  /// Remove one property. The backend owns the choice of what happens to
  /// the transactions and ownership rows that point at it.
  Future<void> deleteProperty(String propertyId);

  /// Bank names the parser accepts.
  ///
  /// Fetched rather than hard-coded: `core/parser.py`'s registry is the
  /// source of truth, and a copy here would drift into offering a bank the
  /// parser refuses.
  Future<List<String>> listBanks();
}
