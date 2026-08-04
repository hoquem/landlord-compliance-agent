// Auth guard and app shell.
//
// **No live Google, and no Supabase.** `AuthSession` is the seam: the app
// takes one, `SupabaseAuthSession` is the only implementation that knows
// about OAuth, and these tests pass a fake. That keeps the guard's actual
// logic (who gets redirected where, and what happens to the route they
// asked for) testable without a network, a browser redirect, or a running
// Supabase.
//
// The guard is worth this much attention because it is the only thing
// standing between an unauthenticated browser and every screen. The API
// refuses unauthenticated requests independently, so a hole here leaks an
// empty shell rather than data, but a shell that renders for a signed-out
// user is still a bug the user would see and not trust.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:landlord_compliance/app.dart';
import 'package:landlord_compliance/features/auth/auth_session.dart';

/// An [AuthSession] with no Supabase behind it.
class FakeAuthSession extends AuthSession {
  FakeAuthSession({bool signedIn = false}) : _signedIn = signedIn;

  bool _signedIn;

  /// How many times sign-in was requested. Asserted rather than the
  /// resulting state, because the button's job is to *ask*: the real
  /// implementation hands off to a browser redirect and returns nothing.
  int signInRequests = 0;

  int signOutRequests = 0;

  @override
  bool get isSignedIn => _signedIn;

  @override
  Future<void> signInWithGoogle() async {
    signInRequests++;
    _signedIn = true;
    notifyListeners();
  }

  @override
  Future<void> signOut() async {
    signOutRequests++;
    _signedIn = false;
    notifyListeners();
  }
}

Future<void> pumpApp(
  WidgetTester tester,
  AuthSession auth, {
  String initialLocation = '/',
  Size size = const Size(1400, 900),
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    LandlordComplianceApp(auth: auth, initialLocation: initialLocation),
  );
  await tester.pumpAndSettle();
}

void main() {
  group('Sign-in', () {
    testWidgets('an unauthenticated visitor gets the sign-in screen', (
      tester,
    ) async {
      await pumpApp(tester, FakeAuthSession());

      expect(find.text('Continue with Google'), findsOneWidget);
    });

    testWidgets('Google is the only way in', (tester) async {
      // Decision 2026-07-29: Google OAuth only, no email/password UI. The
      // email provider stays enabled server-side for RLS test plumbing, and
      // must never surface here.
      // Asserted on affordances, not on words. Matching the string
      // "password" also flags copy that *reassures* there isn't one, which
      // is the opposite of the thing being guarded against.
      await pumpApp(tester, FakeAuthSession());

      expect(find.byType(EditableText), findsNothing);
      expect(find.byType(TextField), findsNothing);
      // A predicate, not find.byType: that matches the exact runtime type,
      // and ButtonStyleButton is abstract. Any button of any kind counts,
      // because the claim is "one action on this screen".
      expect(
        find.byWidgetPredicate((Widget w) => w is ButtonStyleButton),
        findsOneWidget,
      );
    });

    testWidgets('pressing the button asks the session to sign in', (
      tester,
    ) async {
      final FakeAuthSession auth = FakeAuthSession();
      await pumpApp(tester, auth);

      await tester.tap(find.text('Continue with Google'));
      await tester.pumpAndSettle();

      expect(auth.signInRequests, 1);
    });

    testWidgets('the sign-in button holds focus for a keyboard user', (
      tester,
    ) async {
      // Keyboard-complete is a stated requirement (PRODUCT.md), and the
      // first screen is where a keyboard user finds out whether it is true.
      await pumpApp(tester, FakeAuthSession());

      final FocusNode? node = Focus.maybeOf(
        tester.element(find.text('Continue with Google')),
      );
      expect(node, isNotNull);
      expect(node!.hasFocus, isTrue);
    });
  });

  group('The guard', () {
    testWidgets('an unauthenticated deep link does not render the shell', (
      tester,
    ) async {
      await pumpApp(tester, FakeAuthSession(), initialLocation: '/review');

      expect(find.text('Continue with Google'), findsOneWidget);
      expect(find.byType(NavigationRail), findsNothing);
    });

    testWidgets('the route they asked for survives signing in', (tester) async {
      // Someone lands on /certificates from a bookmark, signs in, and should
      // arrive at /certificates. Dropping them on the dashboard instead is
      // the kind of small rudeness that makes a tool feel careless.
      final FakeAuthSession auth = FakeAuthSession();
      await pumpApp(tester, auth, initialLocation: '/certificates');

      await tester.tap(find.text('Continue with Google'));
      await tester.pumpAndSettle();

      // Asserted on the rail's selection, not on the word "Certificates":
      // that word is also a permanent nav label, so a text finder is
      // satisfied by the dashboard. Measured -- with the text assertion,
      // discarding `from` entirely left this test green.
      expect(find.text('Continue with Google'), findsNothing);
      final NavigationRail rail = tester.widget(find.byType(NavigationRail));
      expect(rail.selectedIndex, 3);
    });

    testWidgets('a signed-in visitor at /sign-in is sent to the dashboard', (
      tester,
    ) async {
      await pumpApp(
        tester,
        FakeAuthSession(signedIn: true),
        initialLocation: '/sign-in',
      );

      expect(find.text('Continue with Google'), findsNothing);
      expect(find.byType(NavigationRail), findsOneWidget);
    });

    testWidgets(r'a $from pointing off-origin is refused', (tester) async {
      // Open redirect. Without the check, our own sign-in URL becomes a way
      // to bounce someone to another origin with our name on the link --
      // and the obvious use is a lookalike page asking them to sign in
      // again. Both forms: absolute, and protocol-relative.
      for (final String hostile in <String>[
        '/sign-in?from=https://evil.example.com/x',
        '/sign-in?from=//evil.example.com/x',
      ]) {
        await pumpApp(
          tester,
          FakeAuthSession(signedIn: true),
          initialLocation: hostile,
        );

        expect(
          find.byType(NavigationRail),
          findsOneWidget,
          reason: 'landed somewhere unexpected from $hostile',
        );
        expect(find.text('Dashboard'), findsWidgets);
      }
    });

    testWidgets('an authenticated visitor never sees the sign-in screen', (
      tester,
    ) async {
      await pumpApp(tester, FakeAuthSession(signedIn: true));

      expect(find.text('Continue with Google'), findsNothing);
      expect(find.byType(NavigationRail), findsOneWidget);
    });

    testWidgets('signing out returns to sign-in from any screen', (
      tester,
    ) async {
      final FakeAuthSession auth = FakeAuthSession(signedIn: true);
      await pumpApp(tester, auth, initialLocation: '/imports');

      await auth.signOut();
      await tester.pumpAndSettle();

      expect(find.text('Continue with Google'), findsOneWidget);
      expect(find.byType(NavigationRail), findsNothing);
    });
  });

  group('App shell', () {
    testWidgets('the rail carries exactly the four MVP destinations', (
      tester,
    ) async {
      await pumpApp(tester, FakeAuthSession(signedIn: true));

      for (final String label in <String>[
        'Dashboard',
        'Imports',
        'Review',
        'Certificates',
      ]) {
        expect(find.text(label), findsWidgets, reason: '$label missing');
      }
    });

    testWidgets('the rail marks where you are', (tester) async {
      await pumpApp(
        tester,
        FakeAuthSession(signedIn: true),
        initialLocation: '/review',
      );

      final NavigationRail rail = tester.widget(find.byType(NavigationRail));
      expect(rail.selectedIndex, 2);
    });

    testWidgets('selecting a destination navigates', (tester) async {
      await pumpApp(tester, FakeAuthSession(signedIn: true));

      await tester.tap(find.text('Certificates'));
      await tester.pumpAndSettle();

      final NavigationRail rail = tester.widget(find.byType(NavigationRail));
      expect(rail.selectedIndex, 3);
    });

    testWidgets('a narrow window collapses the rail to icons', (tester) async {
      // Responsive behaviour here is structural, never fluid typography:
      // the labels go, the type does not shrink.
      await pumpApp(
        tester,
        FakeAuthSession(signedIn: true),
        size: const Size(820, 900),
      );

      final NavigationRail rail = tester.widget(find.byType(NavigationRail));
      expect(rail.extended, isFalse);
    });

    testWidgets('a wide window keeps the labels', (tester) async {
      await pumpApp(
        tester,
        FakeAuthSession(signedIn: true),
        size: const Size(1400, 900),
      );

      final NavigationRail rail = tester.widget(find.byType(NavigationRail));
      expect(rail.extended, isTrue);
    });
  });

  group('Theme wiring', () {
    testWidgets('the app opens dark', (tester) async {
      await pumpApp(tester, FakeAuthSession(signedIn: true));

      final MaterialApp app = tester.widget(find.byType(MaterialApp));
      expect(app.themeMode, ThemeMode.dark);
    });

    testWidgets('screens paint on the palette, not a Material default', (
      tester,
    ) async {
      await pumpApp(tester, FakeAuthSession());

      final BuildContext context = tester.element(
        find.text('Continue with Google'),
      );
      expect(Theme.of(context).colorScheme.surfaceTint, Colors.transparent);
    });
  });
}
