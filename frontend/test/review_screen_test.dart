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
    testWidgets('the button counts what it will actually confirm', (
      tester,
    ) async {
      // A line with no category is selectable but not confirmable -- it is
      // dropped silently on the way out. Counting the selection rather than
      // the confirmable set made the button promise work it would not do.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[
          aTxn(id: 'a', category: 'rent_income', confidence: 0.95),
          aTxn(id: 'b', category: null, confidence: null, status: 'unclassified'),
        ];

      await pumpReview(tester, api);
      await tester.tap(find.byType(Checkbox).at(0));
      await tester.tap(find.byType(Checkbox).at(1));
      await tester.pump();

      expect(find.text('Confirm 1'), findsOneWidget);
    });

    testWidgets('the button says how many uncertain proposals it accepts', (
      tester,
    ) async {
      // PRODUCT.md: "Refusing is a feature, so make refusal feel like
      // protection." Refusal is enforced at export; at review, accepting a
      // proposal the agent itself flagged should at least be a thing you
      // notice you are doing.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[
          aTxn(id: 'a', category: 'rent_income', confidence: 0.95),
          aTxn(id: 'b', category: 'repairs_maintenance', confidence: 0.55),
          aTxn(id: 'c', category: 'legal_professional', confidence: 0.62),
        ];

      await pumpReview(tester, api);
      for (int i = 0; i < 3; i++) {
        await tester.tap(find.byType(Checkbox).at(i));
      }
      await tester.pump();

      expect(find.text('Confirm 3 · 2 uncertain'), findsOneWidget);
    });

    testWidgets('a confident-only selection says nothing extra', (tester) async {
      // No noise when there is nothing to say; the warning has to stay rare
      // enough to keep meaning something.
      final FakeApiClient api = FakeApiClient()
        ..txns = <Txn>[aTxn(id: 'a', category: 'rent_income', confidence: 0.95)];

      await pumpReview(tester, api);
      await tester.tap(find.byType(Checkbox).first);
      await tester.pump();

      expect(find.text('Confirm 1'), findsOneWidget);
      expect(find.textContaining('uncertain'), findsNothing);
    });

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

  group('Repayment mortgages', () {
    // A repayment mortgage's direct debit is part interest, part capital, and
    // only the interest is allowable. The bank line is one number, so the
    // figure has to come from a human — and the export refuses without it.
    // The screen's job is to make that resolvable here rather than at the
    // point of being refused. Found on real data, 2026-08-06.
    FakeApiClient repaymentApi() => FakeApiClient()
      ..properties = <PropertyRef>[
        const PropertyRef(id: 'p1', label: '59 Sample Rise', mortgageType: 'repayment'),
      ]
      ..txns = <Txn>[
        aTxn(
          id: 'm1',
          description: 'ONESAVINGS BANK DIRECT DEBIT',
          amount: 1250.00,
          direction: 'out',
          category: 'finance_costs_residential',
          propertyId: 'p1',
          confidence: 0.85,
        ),
      ];

    testWidgets('the row says why it cannot be confirmed yet', (tester) async {
      await pumpReview(tester, repaymentApi());

      expect(find.textContaining('interest'), findsWidgets);
      expect(find.byType(TextField), findsOneWidget);
    });

    testWidgets('it cannot be selected until an interest figure is given', (
      tester,
    ) async {
      // PRODUCT.md: "Refusing is a feature, so make refusal feel like
      // protection." Refusing here, where it is fixable, beats refusing at
      // export where it is a dead end.
      await pumpReview(tester, repaymentApi());

      final Checkbox box = tester.widget(find.byType(Checkbox).first);
      expect(box.onChanged, isNull, reason: 'unconfirmable until split');
    });

    testWidgets('entering the interest makes the row confirmable', (
      tester,
    ) async {
      await pumpReview(tester, repaymentApi());

      await tester.enterText(find.byType(TextField), '412.55');
      await tester.pump();

      final Checkbox box = tester.widget(find.byType(Checkbox).first);
      expect(box.onChanged, isNotNull);
    });

    testWidgets('an interest figure above the payment is refused', (
      tester,
    ) async {
      // The database CHECK would reject it anyway; catching it here means the
      // user finds out while they are looking at the number, not afterwards.
      await pumpReview(tester, repaymentApi());

      await tester.enterText(find.byType(TextField), '2000.00');
      await tester.pump();

      final Checkbox box = tester.widget(find.byType(Checkbox).first);
      expect(box.onChanged, isNull);
      expect(find.textContaining('more than the payment'), findsOneWidget);
    });

    testWidgets('the interest is sent with the confirmation', (tester) async {
      final FakeApiClient api = repaymentApi();
      await pumpReview(tester, api);

      await tester.enterText(find.byType(TextField), '412.55');
      await tester.pump();
      await tester.tap(find.byType(Checkbox).first);
      await tester.pump();
      await tester.tap(find.textContaining('Confirm'));
      await tester.pumpAndSettle();

      expect(api.confirmBatches.single.single.allowableAmount, '412.55');
    });

    testWidgets('an interest-only property needs no figure', (tester) async {
      // The whole payment is interest, so there is nothing to separate and
      // the screen must not invent work.
      final FakeApiClient api = repaymentApi()
        ..properties = <PropertyRef>[
          const PropertyRef(
            id: 'p1',
            label: '59 Sample Rise',
            mortgageType: 'interest_only',
          ),
        ];

      await pumpReview(tester, api);

      expect(find.byType(TextField), findsNothing);
      final Checkbox box = tester.widget(find.byType(Checkbox).first);
      expect(box.onChanged, isNotNull);
    });
  });
}
