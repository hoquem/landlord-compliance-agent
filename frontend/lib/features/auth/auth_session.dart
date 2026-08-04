/// The seam between the app and however a user proves who they are.
///
/// The app depends on this, never on Supabase directly, for one reason:
/// the OAuth flow is a full-page browser redirect and cannot be exercised
/// in a widget test. With the seam here, the route guard's actual logic --
/// who gets redirected, and what happens to the route they asked for --
/// is testable with a fake and no network at all.
///
/// A [ChangeNotifier] because `go_router` takes one as its
/// `refreshListenable`: when this notifies, every redirect re-evaluates.
library;

import 'package:flutter/foundation.dart';

abstract class AuthSession extends ChangeNotifier {
  /// Whether there is a live session right now.
  bool get isSignedIn;

  /// Begin Google sign-in.
  ///
  /// Returns as soon as the flow has been *started*, not when it
  /// completes: on web this hands off to a full-page redirect, so nothing
  /// after the await runs in the same page load. Callers must react to
  /// [isSignedIn] changing, never to this future resolving.
  Future<void> signInWithGoogle();

  /// End the session.
  Future<void> signOut();
}
