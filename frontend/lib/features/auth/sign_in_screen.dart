/// The way in. One button, and nothing to fill in.
///
/// Google OAuth is the only method (decision 2026-07-29), so this screen
/// has no form: no email field, no password field, nothing to get wrong.
/// `test/app_test.dart` asserts the absence, because "we removed the
/// password box" is the sort of thing that quietly comes back.
library;

import 'package:flutter/material.dart';

import '../../theme/tokens.dart';
import 'auth_session.dart';

class SignInScreen extends StatelessWidget {
  const SignInScreen({required this.auth, super.key});

  final AuthSession auth;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;

    return Scaffold(
      body: Center(
        child: ConstrainedBox(
          // Prose caps well short of the window. A sign-in screen centred
          // in 1400px of nothing is the SaaS default; this is a doorway,
          // and a doorway is narrow.
          constraints: const BoxConstraints(maxWidth: 380),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text('Landlord Compliance', style: text.displaySmall),
              const SizedBox(height: Spacing.sm),
              Text(
                'Statements in, quarterly updates out.',
                style: text.bodyLarge?.copyWith(color: palette.textMuted),
              ),
              const SizedBox(height: Spacing.xl),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  // Autofocused so a keyboard user can press Enter on
                  // arrival. There is exactly one action on this screen, so
                  // taking focus cannot steal it from anything.
                  autofocus: true,
                  onPressed: auth.signInWithGoogle,
                  child: const Text('Continue with Google'),
                ),
              ),
              const SizedBox(height: Spacing.md),
              Text(
                'Google is the only way in. There is no password to lose.',
                style: text.bodySmall,
              ),
            ],
          ),
        ),
      ),
    );
  }
}
