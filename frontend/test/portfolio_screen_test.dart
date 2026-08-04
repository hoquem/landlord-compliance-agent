// The portfolio screen, and the ownership editor inside it.
//
// **The ownership rule is the money-critical one.** Per-entity export totals
// derive exclusively from these percentages (HMRC PIM1035), and the database
// deliberately does NOT enforce "sums to 100" — ownership is edited row by
// row through transiently invalid totals, so a DB constraint would fire
// mid-edit.
//
// That makes this the one screen mirroring a backend rule rather than
// rendering a result. The mirror is a courtesy so the user sees the total go
// wrong as they type; the API stays the authority, which is why a rejected
// save still shows the backend's own message.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:landlord_compliance/api/models.dart';
import 'package:landlord_compliance/app.dart';

import 'app_test.dart' show FakeAuthSession;
import 'fake_api.dart';

Future<void> pumpPortfolio(WidgetTester tester, FakeApiClient api) async {
  tester.view.physicalSize = const Size(1500, 950);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    LandlordComplianceApp(
      auth: FakeAuthSession(signedIn: true),
      api: api,
      initialLocation: '/portfolio',
    ),
  );
  await tester.pumpAndSettle();
}

FakeApiClient withPortfolio({List<OwnershipShare>? shares}) => FakeApiClient()
  ..entities = <Entity>[
    const Entity(id: 'e1', name: 'Mahmudul Hoque', taxRegime: 'mtd_itsa'),
    const Entity(id: 'e2', name: 'Sample Co-Owner', taxRegime: 'mtd_itsa'),
  ]
  ..properties = <PropertyRef>[
    const PropertyRef(id: 'p1', label: '98A Sample Rd, LU1 1AA'),
  ]
  ..ownership = <String, List<OwnershipShare>>{
    if (shares != null) 'p1': shares,
  };

Future<void> openOwnership(WidgetTester tester) async {
  await tester.tap(find.text('Ownership').first);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('entities and properties are listed', (tester) async {
    await pumpPortfolio(tester, withPortfolio());

    expect(find.text('Mahmudul Hoque'), findsWidgets);
    expect(find.text('98A Sample Rd, LU1 1AA'), findsOneWidget);
    expect(find.text('MTD ITSA'), findsWidgets);
  });

  testWidgets('the editor loads the shares already stored', (tester) async {
    // PUT replaces the complete set, so an editor that could not read first
    // would reconstruct it from memory -- and getting that wrong is a
    // silently misattributed tax figure.
    await pumpPortfolio(
      tester,
      withPortfolio(
        shares: <OwnershipShare>[
          const OwnershipShare(entityId: 'e1', percentage: 60),
          const OwnershipShare(entityId: 'e2', percentage: 40),
        ],
      ),
    );

    await openOwnership(tester);

    expect(find.text('100.00%'), findsOneWidget);
    expect(find.widgetWithText(TextField, '60.00'), findsOneWidget);
    expect(find.widgetWithText(TextField, '40.00'), findsOneWidget);
  });

  testWidgets('save is blocked while the shares do not total 100', (
    tester,
  ) async {
    await pumpPortfolio(tester, withPortfolio());
    await openOwnership(tester);

    await tester.enterText(find.byKey(const Key('share-e1')), '60');
    await tester.pumpAndSettle();

    expect(find.text('60.00%'), findsOneWidget);
    expect(find.text('must be 100.00%'), findsOneWidget);
    final FilledButton save = tester.widget(
      find.widgetWithText(FilledButton, 'Save ownership'),
    );
    expect(save.onPressed, isNull);
  });

  testWidgets('save unlocks the moment they total 100', (tester) async {
    await pumpPortfolio(tester, withPortfolio());
    await openOwnership(tester);

    await tester.enterText(find.byKey(const Key('share-e1')), '60');
    await tester.enterText(find.byKey(const Key('share-e2')), '40');
    await tester.pumpAndSettle();

    expect(find.text('must be 100.00%'), findsNothing);
    final FilledButton save = tester.widget(
      find.widgetWithText(FilledButton, 'Save ownership'),
    );
    expect(save.onPressed, isNotNull);
  });

  testWidgets('a split that binary arithmetic mis-sums is still accepted', (
    tester,
  ) async {
    // 5.00 + 63.01 + 31.99 sums to 99.99999999999999 as doubles. A bare
    // `== 100` refuses it for reasons that have nothing to do with
    // ownership, so the comparison is made at two decimal places.
    //
    // The example was found by brute force, not guessed: 33.33/33.33/33.34
    // and every other "obvious" case sums to exactly 100.0, and **no
    // two-way split fails at all** -- all 9,999 of them are exact. So a
    // two-entity test could never have caught this, and the first version
    // of this test did not.
    final FakeApiClient api = withPortfolio()
      ..entities = <Entity>[
        const Entity(id: 'e1', name: 'One', taxRegime: 'mtd_itsa'),
        const Entity(id: 'e2', name: 'Two', taxRegime: 'mtd_itsa'),
        const Entity(id: 'e3', name: 'Three', taxRegime: 'mtd_itsa'),
      ];
    await pumpPortfolio(tester, api);
    await openOwnership(tester);

    await tester.enterText(find.byKey(const Key('share-e1')), '5.00');
    await tester.enterText(find.byKey(const Key('share-e2')), '63.01');
    await tester.enterText(find.byKey(const Key('share-e3')), '31.99');
    await tester.pumpAndSettle();

    final FilledButton save = tester.widget(
      find.widgetWithText(FilledButton, 'Save ownership'),
    );
    expect(save.onPressed, isNotNull);
  });

  testWidgets('saving sends the complete set, omitting zero shares', (
    tester,
  ) async {
    final FakeApiClient api = withPortfolio();
    await pumpPortfolio(tester, api);
    await openOwnership(tester);

    await tester.enterText(find.byKey(const Key('share-e1')), '100');
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save ownership'));
    await tester.pumpAndSettle();

    expect(api.savedOwnership.length, 1);
    expect(api.savedOwnership.single.length, 1);
    expect(api.savedOwnership.single.single.entityId, 'e1');
    expect(api.savedOwnership.single.single.percentage, 100);
  });

  testWidgets('the API stays the authority when it refuses', (tester) async {
    // The live check is a courtesy. If the backend disagrees -- for any
    // reason this screen does not model -- its message is what the user sees.
    final FakeApiClient api = withPortfolio()
      ..failSetOwnership = Exception(
        'ownership percentages must sum to 100, got 99.99',
      );
    await pumpPortfolio(tester, api);
    await openOwnership(tester);

    await tester.enterText(find.byKey(const Key('share-e1')), '100');
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save ownership'));
    await tester.pumpAndSettle();

    expect(find.textContaining('must sum to 100, got 99.99'), findsOneWidget);
  });
}
