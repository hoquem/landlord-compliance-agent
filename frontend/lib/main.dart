/// Bootstrap: read configuration, connect Supabase, run the app.
///
/// **Configuration is read loudly.** `SUPABASE_URL` and `SUPABASE_ANON_KEY`
/// arrive as `--dart-define` values at build time; missing ones throw here
/// rather than defaulting to something that half-works and fails later at a
/// network call nobody connects back to a missing flag. That is the same
/// house rule the backend follows for its env vars.
///
///     flutter run -d chrome --web-port 3000 \
///       --dart-define=SUPABASE_URL=... --dart-define=SUPABASE_ANON_KEY=...
///
/// Port 3000 is not arbitrary: `supabase/config.toml` allowlists
/// http://localhost:3000 and http://127.0.0.1:3000 as OAuth redirect
/// targets, and Google refuses anything else. `make web` passes all three.
library;

import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'api/http_api_client.dart';
import 'app.dart';
import 'features/auth/supabase_auth_session.dart';

const String _supabaseUrl = String.fromEnvironment('SUPABASE_URL');
const String _supabaseAnonKey = String.fromEnvironment('SUPABASE_ANON_KEY');
const String _apiBaseUrl = String.fromEnvironment('API_BASE_URL');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  if (_supabaseUrl.isEmpty || _supabaseAnonKey.isEmpty || _apiBaseUrl.isEmpty) {
    throw StateError(
      'SUPABASE_URL, SUPABASE_ANON_KEY and API_BASE_URL must be passed with '
      '--dart-define. Run `make web` from the repo root, which supplies all '
      'three.',
    );
  }

  // `publishableKey`, not the deprecated `anonKey`: Supabase renamed the
  // concept, and the legacy anon JWT this repo's .env carries is accepted
  // under the new name. The env var keeps its old name so the frontend and
  // the backend read the same one.
  await Supabase.initialize(
    url: _supabaseUrl,
    publishableKey: _supabaseAnonKey,
  );

  final SupabaseClient client = Supabase.instance.client;
  runApp(
    LandlordComplianceApp(
      auth: SupabaseAuthSession(client),
      api: HttpApiClient(
        baseUrl: _apiBaseUrl,
        // Read per request, not captured: Supabase refreshes the session in
        // the background, and a captured token goes stale as a 401 on a
        // screen that worked a minute ago.
        accessToken: () => client.auth.currentSession?.accessToken,
      ),
    ),
  );
}
