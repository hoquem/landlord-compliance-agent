/// The real [AuthSession], backed by Supabase Auth.
///
/// **Google is the only method.** Decision of 2026-07-29: no email/password
/// UI. The email provider stays enabled server-side because the RLS tests
/// mint users through the auth-admin API, but it is never reachable from
/// the app.
///
/// Sign-in is *not* awaited to completion anywhere. `signInWithOAuth`
/// navigates the whole page away on web; the session arrives later through
/// `onAuthStateChange`, which is why that stream, not the future, is what
/// drives the router.
library;

import 'dart:async';

import 'package:supabase_flutter/supabase_flutter.dart';

import 'auth_session.dart';

class SupabaseAuthSession extends AuthSession {
  SupabaseAuthSession(this._client) {
    _signedIn = _client.auth.currentSession != null;
    _subscription = _client.auth.onAuthStateChange.listen((AuthState state) {
      final bool next = state.session != null;
      if (next == _signedIn) return;
      _signedIn = next;
      notifyListeners();
    });
  }

  final SupabaseClient _client;
  late final StreamSubscription<AuthState> _subscription;
  bool _signedIn = false;

  @override
  bool get isSignedIn => _signedIn;

  @override
  Future<void> signInWithGoogle() {
    return _client.auth.signInWithOAuth(
      OAuthProvider.google,
      // Come back to wherever the app is served from. `supabase/config.toml`
      // allowlists http://localhost:3000 and http://127.0.0.1:3000, so the
      // dev server has to run on port 3000 or the provider refuses the
      // redirect -- see the Makefile's `web` target.
      redirectTo: Uri.base.origin,
    );
  }

  @override
  Future<void> signOut() => _client.auth.signOut();

  @override
  void dispose() {
    _subscription.cancel();
    super.dispose();
  }
}
