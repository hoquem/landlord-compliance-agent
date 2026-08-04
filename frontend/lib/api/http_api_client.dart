/// The real [ApiClient]: one `http` call per method, no cleverness.
///
/// The bearer token is fetched per request rather than captured once,
/// because Supabase refreshes the session in the background and a captured
/// token goes stale after an hour — as a 401 on a screen that was working a
/// moment ago.
library;

import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'api_client.dart';
import 'models.dart';

class HttpApiClient implements ApiClient {
  HttpApiClient({
    required this.baseUrl,
    required this.accessToken,
    http.Client? client,
  }) : _client = client ?? http.Client();

  final String baseUrl;

  /// Read fresh on every request. See the library docstring.
  final String? Function() accessToken;

  final http.Client _client;

  Map<String, String> get _headers {
    final String? token = accessToken();
    return <String, String>{
      if (token != null) 'Authorization': 'Bearer $token',
    };
  }

  Never _fail(http.Response response) {
    String detail = response.body;
    try {
      final Object? decoded = jsonDecode(response.body);
      if (decoded is Map && decoded['detail'] != null) {
        detail = '${decoded['detail']}';
      }
    } on FormatException {
      // A non-JSON body (a proxy error page, say). The raw text is still
      // more useful to a reader than a generic message, so it stands.
    }
    throw ApiException(response.statusCode, detail);
  }

  Future<Object?> _get(String path) async {
    final http.Response response = await _client.get(
      Uri.parse('$baseUrl$path'),
      headers: _headers,
    );
    if (response.statusCode >= 300) _fail(response);
    return jsonDecode(response.body);
  }

  @override
  Future<List<ImportSummary>> listImports() async {
    final Object? body = await _get('/imports');
    return <ImportSummary>[
      for (final Object? row in body! as List<Object?>)
        ImportSummary.fromJson(row! as Map<String, dynamic>),
    ];
  }

  @override
  Future<List<Entity>> listEntities() async {
    final Object? body = await _get('/entities');
    return <Entity>[
      for (final Object? row in body! as List<Object?>)
        Entity.fromJson(row! as Map<String, dynamic>),
    ];
  }

  @override
  Future<List<String>> listBanks() async {
    final Object? body = await _get('/banks');
    return <String>[for (final Object? row in body! as List<Object?>) row! as String];
  }

  @override
  Future<List<Txn>> listTransactions({String? importId, String? status}) async {
    final Map<String, String> query = <String, String>{
      if (importId != null) 'import_id': importId,
      if (status != null) 'status': status,
    };
    final String suffix = query.isEmpty
        ? ''
        : '?${query.entries.map((MapEntry<String, String> e) => '${e.key}=${Uri.encodeComponent(e.value)}').join('&')}';
    final Object? body = await _get('/transactions$suffix');
    return <Txn>[
      for (final Object? row in body! as List<Object?>)
        Txn.fromJson(row! as Map<String, dynamic>),
    ];
  }

  @override
  Future<List<PropertyRef>> listProperties() async {
    final Object? body = await _get('/properties');
    return <PropertyRef>[
      for (final Object? row in body! as List<Object?>)
        PropertyRef.fromJson(row! as Map<String, dynamic>),
    ];
  }

  @override
  Future<List<String>> listCategories() async {
    final Object? body = await _get('/categories');
    return <String>[for (final Object? row in body! as List<Object?>) row! as String];
  }

  @override
  Future<void> confirmBatch(List<ConfirmItem> items) async {
    final http.Response response = await _client.post(
      Uri.parse('$baseUrl/transactions/confirm-batch'),
      headers: <String, String>{..._headers, 'Content-Type': 'application/json'},
      body: jsonEncode(<String, Object?>{
        'items': <Map<String, dynamic>>[for (final ConfirmItem i in items) i.toJson()],
      }),
    );
    if (response.statusCode >= 300) _fail(response);
  }

  @override
  Future<void> excludeTransaction(String transactionId) async {
    final http.Response response = await _client.post(
      Uri.parse('$baseUrl/transactions/$transactionId/exclude'),
      headers: _headers,
    );
    if (response.statusCode >= 300) _fail(response);
  }

  @override
  Future<ImportSummary> uploadStatement({
    required String entityId,
    required String sourceBank,
    required String filename,
    required Uint8List bytes,
  }) async {
    final http.MultipartRequest request =
        http.MultipartRequest('POST', Uri.parse('$baseUrl/imports'))
          ..headers.addAll(_headers)
          ..fields['entity_id'] = entityId
          ..fields['source_bank'] = sourceBank
          ..files.add(
            http.MultipartFile.fromBytes('file', bytes, filename: filename),
          );

    final http.Response response = await http.Response.fromStream(
      await _client.send(request),
    );
    if (response.statusCode >= 300) _fail(response);
    return ImportSummary.fromJson(
      jsonDecode(response.body) as Map<String, dynamic>,
    );
  }
}
