/// Routes, and the guard that decides who sees them.
///
/// The guard is the only thing between a signed-out browser and every
/// screen. It is not the security boundary -- the API rejects
/// unauthenticated requests independently, and `DATABASE_URL`-side org
/// filtering is what actually protects data -- but a shell that renders for
/// a signed-out user is a bug they can see, and this is where it is
/// prevented.
///
/// **The route someone asked for survives signing in.** A bookmark to
/// `/certificates` redirects to `/sign-in?from=/certificates`, and the
/// return trip honours it. Landing everyone on the dashboard instead is a
/// small rudeness that makes a tool feel careless.
library;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../api/api_client.dart';
import '../features/auth/auth_session.dart';
import '../features/auth/sign_in_screen.dart';
import '../features/certificates/certificates_screen.dart';
import '../features/dashboard/dashboard_screen.dart';
import '../features/imports/imports_screen.dart';
import '../features/review/review_screen.dart';
import 'app_shell.dart';

const String kSignInPath = '/sign-in';

/// Query parameter carrying the route a signed-out visitor was reaching for.
const String kFromParam = 'from';

GoRouter buildRouter({
  required AuthSession auth,
  required ApiClient api,
  String initialLocation = '/',
}) {
  return GoRouter(
    initialLocation: initialLocation,
    // Every redirect re-evaluates whenever the session changes, which is
    // what makes signing in and signing out move the user without any
    // screen having to navigate imperatively.
    refreshListenable: auth,
    redirect: (BuildContext context, GoRouterState state) {
      final bool goingToSignIn = state.matchedLocation == kSignInPath;

      if (!auth.isSignedIn) {
        if (goingToSignIn) return null;
        final String from = state.uri.toString();
        return Uri(
          path: kSignInPath,
          queryParameters: <String, String>{kFromParam: from},
        ).toString();
      }

      if (goingToSignIn) {
        final String? from = state.uri.queryParameters[kFromParam];
        // Only ever an in-app path. An absolute URL here would be an open
        // redirect, turning our own sign-in link into a way to bounce
        // someone to another origin.
        if (from != null && from.startsWith('/') && !from.startsWith('//')) {
          return from;
        }
        return '/';
      }

      return null;
    },
    routes: <RouteBase>[
      GoRoute(
        path: kSignInPath,
        builder: (BuildContext context, GoRouterState state) =>
            SignInScreen(auth: auth),
      ),
      ShellRoute(
        builder: (BuildContext context, GoRouterState state, Widget child) =>
            AppShell(location: state.uri.path, child: child),
        routes: <RouteBase>[
          GoRoute(
            path: '/',
            builder: (BuildContext context, GoRouterState state) =>
                const DashboardScreen(),
          ),
          GoRoute(
            path: '/imports',
            builder: (BuildContext context, GoRouterState state) =>
                ImportsScreen(api: api),
          ),
          GoRoute(
            path: '/review',
            builder: (BuildContext context, GoRouterState state) =>
                ReviewScreen(api: api),
          ),
          GoRoute(
            path: '/certificates',
            builder: (BuildContext context, GoRouterState state) =>
                const CertificatesScreen(),
          ),
        ],
      ),
    ],
  );
}
