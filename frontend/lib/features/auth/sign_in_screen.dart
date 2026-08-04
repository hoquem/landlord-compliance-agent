/// The way in. One button, and nothing to fill in.
///
/// Google OAuth is the only method (decision 2026-07-29), so this screen has
/// no form: no email field, no password field, nothing to get wrong.
/// `test/app_test.dart` asserts the absence on affordances rather than on the
/// word "password", which also matches the copy reassuring you there isn't
/// one.
///
/// **Composition: anchored, not centred.** The first version put a 380px
/// column dead-centre in a 1440px void — a small thing floating in a large
/// nothing, which is the SaaS default this project's anti-references rule
/// out. The content is now anchored to the left edge with a deliberate
/// inset, so the empty space to the right reads as chosen rather than left
/// over, and the wordmark is large enough to carry the screen on its own.
///
/// **Three states, because a redirect is invisible.** `signInWithOAuth`
/// navigates the whole page away, and until the browser gets round to it the
/// screen would otherwise sit there looking broken. Pressing the button
/// shows `Opening Google`; a failure says what broke and how to retry.
library;

import 'package:flutter/material.dart';

import '../../theme/tokens.dart';
import 'auth_session.dart';

class SignInScreen extends StatefulWidget {
  const SignInScreen({required this.auth, super.key});

  final AuthSession auth;

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  bool _pending = false;
  String? _error;

  Future<void> _signIn() async {
    // Guard against a second press: the button is disabled while pending,
    // but a keyboard repeat or a double tap can still land before the frame.
    if (_pending) return;
    setState(() {
      _pending = true;
      _error = null;
    });
    try {
      await widget.auth.signInWithGoogle();
    } catch (error) {
      // Nothing narrower to catch: this crosses into Supabase, then a
      // browser redirect. What matters is that whatever comes back is shown
      // rather than swallowed, so the screen never silently does nothing.
      if (!mounted) return;
      setState(() {
        _pending = false;
        _error = '$error';
      });
      return;
    }
    // No success branch. On the happy path the router's redirect moves us
    // away the moment the session changes, and on web the page has usually
    // navigated to Google before this line is reachable at all.
    if (mounted && widget.auth.isSignedIn == false) {
      setState(() => _pending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;

    return Scaffold(
      body: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: Spacing.xxl + Spacing.md,
          vertical: Spacing.xxl,
        ),
        child: Align(
          alignment: Alignment.centerLeft,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 560),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  'Landlord Compliance',
                  style: AppType.wordmark.copyWith(color: palette.textHigh),
                ),
                const SizedBox(height: Spacing.lg),
                // A hairline, doing the work a card would otherwise be
                // reached for: it gives the block an edge without boxing it.
                Container(height: 1, color: palette.rule),
                const SizedBox(height: Spacing.lg),
                Text(
                  'Statements in, quarterly updates out.',
                  style: text.bodyLarge?.copyWith(color: palette.textMuted),
                ),
                const SizedBox(height: Spacing.xl),
                if (_error != null) ...<Widget>[
                  _SignInError(message: _error!),
                  const SizedBox(height: Spacing.lg),
                ],
                FilledButton(
                  // Autofocused so a keyboard user can press Enter on
                  // arrival. One action on the screen, so it cannot steal
                  // focus from anything.
                  autofocus: true,
                  onPressed: _pending ? null : _signIn,
                  child: Text(_pending ? 'Opening Google' : 'Continue with Google'),
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
      ),
    );
  }
}

/// What went wrong, and what to do about it.
///
/// A full border rather than a coloured left edge: side-stripe borders are
/// banned in `DESIGN.md`, and they read as decoration on a message that is
/// meant to be read.
class _SignInError extends StatelessWidget {
  const _SignInError({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;

    return Container(
      padding: const EdgeInsets.all(Spacing.md),
      decoration: BoxDecoration(
        color: palette.bgSurface,
        border: Border.all(color: palette.dangerDim),
        borderRadius: Radii.smRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            "Google sign-in didn't start.",
            style: text.titleMedium?.copyWith(color: palette.danger),
          ),
          const SizedBox(height: Spacing.xs),
          Text(
            '$message. Check you are online, then try again.',
            style: text.bodyMedium,
          ),
        ],
      ),
    );
  }
}
