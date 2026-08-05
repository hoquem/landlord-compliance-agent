// The dashboard.
//
// Two things carry the risk here.
//
// The **deadline is rendered, never computed**. It arrives from the API,
// which calls core/quarters.py — the one implementation, unit-tested against
// all four statutory dates. A second one in Dart would be a second opinion
// about a tax deadline, and the failure mode is filing late.
//
// The **refusal path is the product working**. An export blocked by
// unreviewed lines must read as protection with a way forward, not as an
// obstacle.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:landlord_compliance/api/models.dart';
import 'package:landlord_compliance/app.dart';
import 'package:landlord_compliance/theme/tokens.dart';

import 'app_test.dart' show FakeAuthSession;
import 'fake_api.dart';

Future<void> pumpDashboard(WidgetTester tester, FakeApiClient api) async {
  tester.view.physicalSize = const Size(1500, 950);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    LandlordComplianceApp(
      auth: FakeAuthSession(signedIn: true),
      api: api,
      initialLocation: '/',
    ),
  );
  await tester.pumpAndSettle();
}

DashboardSummary summary({
  int needsDecision = 0,
  int days = 95,
  DateTime? deadline,
  int expiring = 0,
  int expired = 0,
  int unreadableImports = 0,
  int uncategorisedImports = 0,
}) => DashboardSummary(
  needsDecision: needsDecision,
  nextDeadline: deadline ?? DateTime.utc(2026, 11, 7),
  daysUntilDeadline: days,
  expiringCertificates: expiring,
  expiredCertificates: expired,
  unreadableImports: unreadableImports,
  uncategorisedImports: uncategorisedImports,
);

void main() {
  group('The deadline', () {
    testWidgets('is rendered from the API, in words', (tester) async {
      final FakeApiClient api = FakeApiClient()..summary = summary(days: 95);

      await pumpDashboard(tester, api);

      expect(
        find.text('Your next update is due on 7 November, in 95 days.'),
        findsOneWidget,
      );
    });

    // One pump per case. Pumping a second app into the same tester reuses
    // the State -- same widget type in the same position -- so initState
    // never re-runs and the screen keeps the first summary. Measured: a
    // three-case loop passed its first iteration and failed the second.
    for (final (int days, String expected) in <(int, String)>[
      (0, 'Your next update is due today.'),
      (1, 'Your next update is due tomorrow, 7 November.'),
      (-3, 'Your last update was due on 7 November.'),
    ]) {
      testWidgets('reads naturally at $days days out', (tester) async {
        final FakeApiClient api = FakeApiClient()..summary = summary(days: days);

        await pumpDashboard(tester, api);

        expect(find.text(expected), findsOneWidget);
      });
    }

    testWidgets('a distant deadline recedes into the background', (
      tester,
    ) async {
      // Settled work goes quiet. A deadline three months out is information,
      // not a task.
      final FakeApiClient api = FakeApiClient()..summary = summary(days: 95);

      await pumpDashboard(tester, api);

      final Text line = tester.widget(find.textContaining('Your next update'));
      expect(line.style!.color, Palette.dark.textMuted);
    });

    testWidgets('a near deadline holds full contrast', (tester) async {
      final FakeApiClient api = FakeApiClient()..summary = summary(days: 5);

      await pumpDashboard(tester, api);

      final Text line = tester.widget(find.textContaining('Your next update'));
      expect(line.style!.color, Palette.dark.textHigh);
    });
  });

  group('What needs you', () {
    testWidgets('outstanding review is stated and linked', (tester) async {
      final FakeApiClient api = FakeApiClient()
        ..summary = summary(needsDecision: 47);

      await pumpDashboard(tester, api);

      expect(find.text('47 lines need a decision.'), findsOneWidget);
      expect(find.text('Review'), findsWidgets);
    });

    testWidgets('one of anything reads as one, not "1 lines"', (tester) async {
      final FakeApiClient api = FakeApiClient()
        ..summary = summary(
          needsDecision: 1,
          expiring: 1,
          expired: 1,
          unreadableImports: 1,
          uncategorisedImports: 1,
        );

      await pumpDashboard(tester, api);

      expect(find.text('1 line needs a decision.'), findsOneWidget);
      expect(find.text('1 certificate lapses within 60 days.'), findsOneWidget);
      expect(find.text('1 certificate has lapsed.'), findsOneWidget);
      expect(find.text('1 import could not be read.'), findsOneWidget);
      expect(
        find.text('1 import was read but could not be categorised.'),
        findsOneWidget,
      );
    });

    testWidgets('an unreadable file and an uncategorised one are not the same', (
      tester,
    ) async {
      // Seen for real 2026-08-05: one file genuinely could not be parsed, and
      // one was parsed perfectly before the model fell over. The screen said
      // "2 imports could not be read" — half false, and it buried the one
      // whose data is fine and sitting there waiting.
      final FakeApiClient api = FakeApiClient()
        ..summary = summary(unreadableImports: 1, uncategorisedImports: 1);

      await pumpDashboard(tester, api);

      expect(find.text('2 imports could not be read.'), findsNothing);
      expect(find.text('1 import could not be read.'), findsOneWidget);
      expect(
        find.text('1 import was read but could not be categorised.'),
        findsOneWidget,
      );
    });

    testWidgets('a lapsed certificate reads as wrong, not merely pending', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()..summary = summary(expired: 2);

      await pumpDashboard(tester, api);

      final Text line = tester.widget(find.text('2 certificates have lapsed.'));
      expect(line.style!.color, Palette.dark.danger);
    });

    testWidgets('nothing outstanding says so plainly', (tester) async {
      await pumpDashboard(tester, FakeApiClient()..summary = summary());

      expect(find.text('Nothing else is waiting on you.'), findsOneWidget);
    });

    testWidgets('it is not a grid of stat cards', (tester) async {
      // "Generic SaaS dashboard" is a named anti-reference, and DESIGN.md
      // bans identical card grids and the hero-metric template outright.
      final FakeApiClient api = FakeApiClient()
        ..summary = summary(needsDecision: 47, expiring: 2);

      await pumpDashboard(tester, api);

      expect(find.byType(Card), findsNothing);
    });
  });

  group('Exporting', () {
    testWidgets('a blocked export shows the reason and offers the fix', (
      tester,
    ) async {
      // A refusal must read as protection, not an obstacle: the backend
      // names the transactions, and the fix is one click away.
      final FakeApiClient api = FakeApiClient()
        ..entities = <Entity>[
          const Entity(id: 'e1', name: 'Owner', taxRegime: 'mtd_itsa'),
        ]
        ..failExport = Exception(
          '3 transaction(s) in this period are not yet reviewed: a, b, c',
        );

      await pumpDashboard(tester, api);
      await tester.tap(find.text('Export a quarter'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Generate'));
      await tester.pumpAndSettle();

      expect(find.textContaining('not yet reviewed'), findsOneWidget);
      expect(find.text('Go and review them'), findsOneWidget);
    });

    testWidgets('a successful export offers each generated file', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()
        ..entities = <Entity>[
          const Entity(id: 'e1', name: 'Owner', taxRegime: 'mtd_itsa'),
        ];

      await pumpDashboard(tester, api);
      await tester.tap(find.text('Export a quarter'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Generate'));
      await tester.pumpAndSettle();

      expect(find.text('2026-27 Q1 is ready · version 1.'), findsOneWidget);
      expect(find.text('Return figures (CSV)'), findsOneWidget);
      expect(find.text('Summary (PDF)'), findsOneWidget);
    });

    testWidgets('a file is fetched through a signed URL, not a raw path', (
      tester,
    ) async {
      // The export buckets are private on purpose. A path alone would 403.
      final FakeApiClient api = FakeApiClient()
        ..entities = <Entity>[
          const Entity(id: 'e1', name: 'Owner', taxRegime: 'mtd_itsa'),
        ];

      await pumpDashboard(tester, api);
      await tester.tap(find.text('Export a quarter'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Generate'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Summary (PDF)'));
      await tester.pumpAndSettle();

      expect(api.downloadedDocuments, <String>['d2']);
    });
  });
}
