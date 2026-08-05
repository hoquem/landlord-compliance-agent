// The certificates screen.
//
// **Status is derived on every read, never stored.** The API recomputes it
// per request and this screen only renders it — so what is asserted here is
// that the three states are distinguishable, and that each is paired with
// its word rather than carried by colour alone.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:landlord_compliance/api/models.dart';
import 'package:landlord_compliance/app.dart';
import 'package:landlord_compliance/theme/status_colors.dart';

import 'app_test.dart' show FakeAuthSession;
import 'fake_api.dart';

Future<void> pumpCertificates(WidgetTester tester, FakeApiClient api) async {
  tester.view.physicalSize = const Size(1500, 950);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    LandlordComplianceApp(
      auth: FakeAuthSession(signedIn: true),
      api: api,
      initialLocation: '/certificates',
    ),
  );
  await tester.pumpAndSettle();
}

FakeApiClient withCertificates(List<Certificate> certificates) =>
    FakeApiClient()
      ..properties = <PropertyRef>[
        const PropertyRef(id: 'p1', label: '98A Sample Rd'),
      ]
      ..certificateGroups = <PropertyCertificates>[
        PropertyCertificates(propertyId: 'p1', certificates: certificates),
      ];

void main() {
  testWidgets('certificates are grouped under their property', (tester) async {
    await pumpCertificates(
      tester,
      withCertificates(<Certificate>[
        aCertificate(type: 'gas_safety'),
        aCertificate(id: 'c2', type: 'eicr'),
      ]),
    );

    expect(find.text('98A Sample Rd'), findsOneWidget);
    expect(find.text('Gas safety'), findsOneWidget);
    // Was `find.text('Eicr')` until 2026-08-05 — this test *pinned the bug*.
    // It asserted the label as derivation happened to produce it, so the
    // screen could read "Eicr" beneath a subtitle reading "EICR" and the
    // suite stayed green. Changed deliberately, with the behaviour.
    expect(find.text('EICR'), findsOneWidget);
  });

  testWidgets('each state carries its word, not just a colour', (tester) async {
    await pumpCertificates(
      tester,
      withCertificates(<Certificate>[
        aCertificate(id: 'a', status: 'valid'),
        aCertificate(id: 'b', type: 'eicr', status: 'expiring'),
        aCertificate(id: 'c', type: 'epc', status: 'expired'),
      ]),
    );

    expect(find.text('Valid'), findsOneWidget);
    expect(find.text('Expiring'), findsOneWidget);
    expect(find.text('Expired'), findsOneWidget);
  });

  testWidgets('expiring asks for attention; valid recedes', (tester) async {
    // Valid is settled: it should not compete with the one thing that needs
    // booking an engineer.
    await pumpCertificates(
      tester,
      withCertificates(<Certificate>[
        aCertificate(id: 'a', status: 'valid'),
        aCertificate(id: 'b', type: 'eicr', status: 'expiring'),
        aCertificate(id: 'c', type: 'epc', status: 'expired'),
      ]),
    );

    final BuildContext context = tester.element(find.text('Valid'));
    final StatusColors colors = Theme.of(context).extension<StatusColors>()!;

    expect((tester.widget(find.text('Valid')) as Text).style!.color, colors.settled);
    expect(
      (tester.widget(find.text('Expiring')) as Text).style!.color,
      colors.needsYou,
    );
    expect(
      (tester.widget(find.text('Expired')) as Text).style!.color,
      colors.wrong,
    );
  });

  testWidgets('an empty state teaches what the page is for', (tester) async {
    await pumpCertificates(tester, FakeApiClient());

    expect(find.text('No certificates recorded.'), findsOneWidget);
    expect(find.textContaining('what lapses next'), findsOneWidget);
  });

  testWidgets('the add form is inline, and needs a property and a date', (
    tester,
  ) async {
    final FakeApiClient api = withCertificates(<Certificate>[aCertificate()]);
    await pumpCertificates(tester, api);

    await tester.tap(find.text('Add certificate'));
    await tester.pumpAndSettle();

    expect(find.byType(Dialog), findsNothing);
    final FilledButton save = tester.widget(
      find.widgetWithText(FilledButton, 'Save'),
    );
    expect(save.onPressed, isNull);
  });

  testWidgets('removing a certificate calls the API and drops the row', (
    tester,
  ) async {
    // A superseded or mis-entered certificate has no value to keep; what it
    // was survives in the audit row.
    final FakeApiClient api = withCertificates(<Certificate>[
      aCertificate(id: 'gone'),
    ]);
    await pumpCertificates(tester, api);

    await tester.tap(find.byIcon(Icons.close));
    await tester.pumpAndSettle();

    expect(api.deletedCertificates, <String>['gone']);
    expect(find.text('Gas safety'), findsNothing);
  });
}
