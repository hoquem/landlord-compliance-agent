/// The application widget.
///
/// Takes its [AuthSession] rather than reaching for Supabase, so widget
/// tests can drive the whole app -- guard included -- with a fake and no
/// network. `main.dart` is the only place that knows Supabase exists.
library;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import 'app/app_router.dart';
import 'features/auth/auth_session.dart';
import 'theme/app_theme.dart';

class LandlordComplianceApp extends StatefulWidget {
  const LandlordComplianceApp({
    required this.auth,
    this.initialLocation = '/',
    super.key,
  });

  final AuthSession auth;
  final String initialLocation;

  @override
  State<LandlordComplianceApp> createState() => _LandlordComplianceAppState();
}

class _LandlordComplianceAppState extends State<LandlordComplianceApp> {
  late final GoRouter _router = buildRouter(
    auth: widget.auth,
    initialLocation: widget.initialLocation,
  );

  @override
  void dispose() {
    _router.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'Landlord Compliance',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      // Dark from the scene, not from taste: a home office in the evening,
      // lamp on, dim room. See PRODUCT.md.
      themeMode: AppTheme.defaultThemeMode,
      routerConfig: _router,
    );
  }
}
