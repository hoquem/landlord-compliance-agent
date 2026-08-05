// The review queue: the screen this product exists for.
//
// Three claims carry most of the risk, and each has a test that dies if it
// stops being true:
//
//   * Proposal leads, evidence beneath. The raw bank narrative is always
//     visible, because the user is checking a machine.
//   * Confidence changes what you see FIRST, never what appears true. It
//     drives order and weight, and is never a colour.
//   * A batch confirm is ONE call. Row-by-row would look identical on
//     screen while hammering the API and losing the all-or-nothing
//     guarantee the backend provides.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:landlord_compliance/api/models.dart';
import 'package:landlord_compliance/api/money.dart';
import 'package:landlord_compliance/app.dart';
import 'package:landlord_compliance/theme/tokens.dart';

import 'app_test.dart' show FakeAuthSession;
import 'fake_api.dart';

Future<void> pumpReview(WidgetTester tester, FakeApiClient api) async {
  tester.view.physicalSize = const Size(1500, 950);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    LandlordComplianceApp(
      auth: FakeAuthSession(signedIn: true),
      api: api,
      initialLocation: '/review',
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  group('The row', () {
    testWidgets('the proposal leads and the bank text is always visible', (
      tester,
    ) async {
      // The user is checking a machine's answer. Hiding the evidence in a
      // tooltip would make that impossible without a hover.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(category: 'rent_income')]
        ..properties = <PropertyRef>[
          const PropertyRef(id: 'p1', label: '98A Sample Rd'),
        ];

      await pumpReview(tester, api);

      expect(find.text('Rent income'), findsOneWidget);
      expect(
        find.text('SAMPLE ESTATES L 98A SAMPLE ROAD BGC'),
        findsOneWidget,
      );
    });

    testWidgets('the property joins the proposal line when there is one', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(propertyId: 'p1')]
        ..properties = <PropertyRef>[
          const PropertyRef(id: 'p1', label: '98A Sample Rd'),
        ];

      await pumpReview(tester, api);

      expect(find.text('Rent income · 98A Sample Rd'), findsOneWidget);
    });

    testWidgets('money out shows a real minus sign, not a hyphen', (
      tester,
    ) async {
      // A hyphen is narrower than a digit, so one negative amount breaks the
      // alignment of a whole column.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(amount: 1134.60, direction: 'out')];

      await pumpReview(tester, api);

      expect(find.text('${kMinus}1,134.60'), findsOneWidget);
      expect(find.text('-1,134.60'), findsNothing);
    });

    testWidgets('an amount is never coloured by its sign', (tester) async {
      // An expense is not an error. `danger` means something is wrong, and
      // spending money on a roof is not.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[
          aTxn(id: 'a', amount: 950, direction: 'in'),
          aTxn(id: 'b', amount: 84.99, direction: 'out'),
        ];

      await pumpReview(tester, api);

      final Text incoming = tester.widget(find.text('950.00'));
      final Text outgoing = tester.widget(find.text('${kMinus}84.99'));
      expect(incoming.style!.color, outgoing.style!.color);
      expect(outgoing.style!.color, isNot(Palette.dark.danger));
    });

    testWidgets('every amount is tabular with a slashed zero', (tester) async {
      final FakeApiClient api = FakeApiClient()..txns = <Txn>[aTxn()];

      await pumpReview(tester, api);

      final Text amount = tester.widget(find.text('950.00'));
      expect(amount.style!.fontFeatures, AppType.tabular);
    });
  });

  group('Confidence', () {
    testWidgets('an uncertain proposal sorts above a confident one', (
      tester,
    ) async {
      // Proposal-leads means the machine's answer is what you read first,
      // which makes the uncertain ones the dangerous ones.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[
          aTxn(id: 'sure', confidence: 0.97, category: 'rent_income'),
          aTxn(id: 'unsure', confidence: 0.41, category: 'repairs_maintenance'),
        ];

      await pumpReview(tester, api);

      final double unsureY = tester.getTopLeft(find.text('Repairs maintenance')).dy;
      final double sureY = tester.getTopLeft(find.text('Rent income')).dy;
      expect(unsureY, lessThan(sureY));
    });

    testWidgets('an uncategorised line sorts above even an uncertain one', (
      tester,
    ) async {
      // No proposal at all is the least certain state there is.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[
          aTxn(id: 'unsure', confidence: 0.3, category: 'rent_income'),
          aTxn(
            id: 'none',
            status: 'unclassified',
            category: null,
            confidence: null,
          ),
        ];

      await pumpReview(tester, api);

      expect(
        tester.getTopLeft(find.text('Choose a category')).dy,
        lessThan(tester.getTopLeft(find.text('Rent income')).dy),
      );
    });

    testWidgets('low confidence is weight and a word, never a tint', (
      tester,
    ) async {
      // Colouring it would collide with the status vocabulary and imply the
      // agent did something wrong by being unsure.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(confidence: 0.41)];

      await pumpReview(tester, api);

      final Text proposal = tester.widget(find.text('Rent income'));
      expect(proposal.style!.fontWeight, FontWeight.w400);
      expect(proposal.style!.color, Palette.dark.textHigh);
      expect(find.text('check this'), findsOneWidget);
    });

    testWidgets('a confirmed line stops asking to be checked', (tester) async {
      // "check this" is an instruction, and once a human has confirmed the
      // line the instruction is stale -- they *did* check it. The flag was
      // driven by confidence alone, which never changes, so five settled rows
      // sat there still nagging. Seen on a real quarter, 2026-08-05.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(confidence: 0.41, status: 'confirmed')];

      await pumpReview(tester, api);

      expect(find.text('Rent income'), findsOneWidget);
      expect(find.text('check this'), findsNothing);
    });

    testWidgets('a confident proposal is semibold and unflagged', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(confidence: 0.95)];

      await pumpReview(tester, api);

      final Text proposal = tester.widget(find.text('Rent income'));
      expect(proposal.style!.fontWeight, FontWeight.w600);
      expect(find.text('check this'), findsNothing);
    });

    testWidgets('the flag threshold is 0.8, checked at both sides', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[
          aTxn(id: 'below', confidence: 0.79, category: 'rent_income'),
          aTxn(id: 'at', confidence: 0.80, category: 'repairs_maintenance'),
        ];

      await pumpReview(tester, api);

      expect(find.text('check this'), findsOneWidget);
      expect(kLowConfidence, 0.8);
    });
  });

  group('Confirming', () {
    testWidgets('a batch confirm is one call, not one per row', (tester) async {
      // Row-by-row would look identical on screen while hammering the API
      // and throwing away the backend's all-or-nothing guarantee.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[
          aTxn(id: 'a', category: 'rent_income'),
          aTxn(id: 'b', category: 'repairs_maintenance'),
          aTxn(id: 'c', category: 'rent_income'),
        ];

      await pumpReview(tester, api);
      for (int i = 0; i < 3; i++) {
        await tester.tap(find.byType(Checkbox).at(i));
      }
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirm 3'));
      await tester.pumpAndSettle();

      expect(api.confirmBatches.length, 1);
      expect(api.confirmBatches.single.length, 3);
    });

    testWidgets('confirmed rows recede so the screen calms down', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(id: 'a', category: 'rent_income')];

      await pumpReview(tester, api);
      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirm 1'));
      await tester.pumpAndSettle();

      final Text proposal = tester.widget(find.text('Rent income'));
      expect(proposal.style!.color, Palette.dark.textMuted);
      expect(find.text('Confirmed'), findsOneWidget);
    });

    testWidgets('clearing the queue is marked, once, without confetti', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(id: 'a', category: 'rent_income')];

      await pumpReview(tester, api);
      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirm 1'));
      await tester.pumpAndSettle();

      expect(find.text("That's everything reviewed."), findsOneWidget);
    });

    testWidgets('a settled row cannot be re-selected', (tester) async {
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(status: 'confirmed', category: 'rent_income')];

      await pumpReview(tester, api);

      final Checkbox box = tester.widget(find.byType(Checkbox).first);
      expect(box.onChanged, isNull);
    });

    testWidgets('a failed confirm says why and changes nothing', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(id: 'a', category: 'rent_income')]
        ..failConfirm = Exception(
          'Cannot confirm against this property: shares sum to 50',
        );

      await pumpReview(tester, api);
      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirm 1'));
      await tester.pumpAndSettle();

      expect(find.textContaining('shares sum to 50'), findsOneWidget);
      expect(find.text('Proposed'), findsOneWidget);
    });

    testWidgets('the header counts what still needs a decision', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[
          aTxn(id: 'a', status: 'proposed'),
          aTxn(id: 'b', status: 'unclassified', category: null),
          aTxn(id: 'c', status: 'confirmed'),
        ];

      await pumpReview(tester, api);

      expect(find.text('2 lines need a decision.'), findsOneWidget);
    });
  });

  group('The category picker', () {
    testWidgets('is a popover, not a bottom sheet', (tester) async {
      // The plan asked for a sheet. Correcting one proposal must not cover
      // the queue you are working through.
      final FakeApiClient api = FakeApiClient()..txns = <Txn>[aTxn()];

      await pumpReview(tester, api);
      await tester.tap(find.text('Rent income'));
      await tester.pumpAndSettle();

      expect(find.byType(BottomSheet), findsNothing);
      expect(find.text('Filter categories'), findsOneWidget);
    });

    testWidgets('filters as you type and applies the choice', (tester) async {
      final FakeApiClient api = FakeApiClient()..txns = <Txn>[aTxn()];

      await pumpReview(tester, api);
      await tester.tap(find.text('Rent income'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'repairs');
      await tester.pumpAndSettle();

      expect(find.text('Repairs maintenance'), findsOneWidget);
      expect(find.text('Use of home allowance'), findsNothing);

      await tester.tap(find.text('Repairs maintenance'));
      await tester.pumpAndSettle();

      expect(find.text('Repairs maintenance'), findsOneWidget);
    });

    testWidgets('choosing a category selects the row for confirming', (
      tester,
    ) async {
      // Correcting a line is a decision about it, so it should not then need
      // a separate click to be included in the batch.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(status: 'unclassified', category: null)];

      await pumpReview(tester, api);
      await tester.tap(find.text('Choose a category'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Rent income'));
      await tester.pumpAndSettle();

      expect(find.text('Confirm 1'), findsOneWidget);
    });

    testWidgets('the categories come from the API, not a list in Dart', (
      tester,
    ) async {
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn()]
        ..categories = <String>['travel_vehicle'];

      await pumpReview(tester, api);
      await tester.tap(find.text('Rent income'));
      await tester.pumpAndSettle();

      expect(find.text('Travel vehicle'), findsOneWidget);
      expect(find.text('Repairs maintenance'), findsNothing);
    });
  });
}
