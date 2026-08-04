// The imports screen.
//
// **The failure path is the point of this screen**, not an edge case of it.
// The backend goes to real trouble to name the row that broke and why, and
// the spec says this failure UX matters more than format coverage. Most of
// what is asserted below is therefore about what a *refused* file looks
// like.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:landlord_compliance/app.dart';
import 'package:landlord_compliance/app/widgets/status_pill.dart';
import 'package:landlord_compliance/api/models.dart';
import 'package:landlord_compliance/theme/status_colors.dart';

import 'app_test.dart' show FakeAuthSession;
import 'fake_api.dart';

Future<void> pumpImports(
  WidgetTester tester,
  FakeApiClient api, {
  Size size = const Size(1400, 900),
}) async {
  tester.view.physicalSize = size;
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    LandlordComplianceApp(
      auth: FakeAuthSession(signedIn: true),
      api: api,
      initialLocation: '/imports',
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('imports are listed with a status word, not just a colour', (
    tester,
  ) async {
    final FakeApiClient api = FakeApiClient(
      imports: <dynamic>[anImport(sourceBank: 'barclays')].cast(),
    );

    await pumpImports(tester, api);

    expect(find.text('barclays'), findsOneWidget);
    expect(find.text('Imported'), findsOneWidget);
    expect(find.byType(StatusPill), findsOneWidget);
  });

  testWidgets('a failed import names the row that broke it', (tester) async {
    // Not "import failed". The row number is the actionable part, and
    // flattening it away throws the backend's honesty on the floor.
    final FakeApiClient api = FakeApiClient(
      imports: <dynamic>[
        anImport(
          status: 'failed',
          errorDetail: <String, dynamic>{
            'row_number': 14,
            'message': "unparseable date: '32/13/2026'",
          },
        ),
      ].cast(),
    );

    await pumpImports(tester, api);

    expect(find.text('Row 14 stopped this import.'), findsOneWidget);
    expect(find.textContaining('32/13/2026'), findsOneWidget);
    expect(find.textContaining('upload again'), findsOneWidget);
  });

  testWidgets('a failure with no row number still says something useful', (
    tester,
  ) async {
    // Format mismatch and decode failures have no row: the whole file was
    // wrong. The screen must not print "Row null".
    final FakeApiClient api = FakeApiClient(
      imports: <dynamic>[
        anImport(
          status: 'failed',
          errorDetail: <String, dynamic>{
            'row_number': null,
            'message': 'file does not match the format for bank barclays',
          },
        ),
      ].cast(),
    );

    await pumpImports(tester, api);

    expect(find.text('This import could not be read.'), findsOneWidget);
    expect(find.textContaining('Row null'), findsNothing);
  });

  testWidgets('a failed categorisation is visible, not silently pending', (
    tester,
  ) async {
    // Task 18 pushes categorisation_failed onto the import precisely so this
    // screen can show it. An import stuck mid-pipeline must never read as
    // healthy.
    final FakeApiClient api = FakeApiClient(
      imports: <dynamic>[anImport(status: 'categorisation_failed')].cast(),
    );

    await pumpImports(tester, api);

    expect(find.text('Categorising failed'), findsOneWidget);
  });

  testWidgets('the wrong state uses the danger colour, the settled one does not', (
    tester,
  ) async {
    final FakeApiClient api = FakeApiClient(
      imports: <dynamic>[
        anImport(id: 'a', status: 'parsed'),
        anImport(id: 'b', status: 'failed'),
      ].cast(),
    );

    await pumpImports(tester, api);

    final BuildContext context = tester.element(find.text('Imported'));
    final StatusColors colors = Theme.of(context).extension<StatusColors>()!;
    final Text settled = tester.widget(find.text('Imported'));
    final Text wrong = tester.widget(find.text('Failed'));

    expect(settled.style!.color, colors.settled);
    expect(wrong.style!.color, colors.wrong);
    // Settled recedes. If these were equal the screen would never calm down.
    expect(colors.settled, isNot(colors.wrong));
  });

  testWidgets('an empty list teaches instead of saying "no results"', (
    tester,
  ) async {
    await pumpImports(tester, FakeApiClient());

    expect(find.text('No statements yet.'), findsOneWidget);
    expect(find.textContaining('Export a CSV from your bank'), findsOneWidget);
  });

  testWidgets('a load failure is recoverable', (tester) async {
    // An error state you cannot leave is worse than no error state.
    final FakeApiClient api = FakeApiClient()
      ..failListImports = Exception('connection refused');

    await pumpImports(tester, api);
    expect(find.textContaining('connection refused'), findsOneWidget);

    api.failListImports = null;
    api.imports = <dynamic>[anImport()].cast();
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();

    expect(find.text('barclays'), findsOneWidget);
  });

  testWidgets('the upload form is inline, never a modal', (tester) async {
    // DESIGN.md bans the modal-as-first-thought, and this is the case that
    // proves it useful: the form adds a row to the list behind it, and a
    // modal would hide that list exactly when it matters.
    await pumpImports(tester, FakeApiClient());

    await tester.tap(find.text('Add statement'));
    await tester.pumpAndSettle();

    expect(find.text('Add a statement'), findsOneWidget);
    expect(find.byType(Dialog), findsNothing);
    expect(find.byType(BottomSheet), findsNothing);
  });

  testWidgets('upload stays disabled until entity, bank and file are chosen', (
    tester,
  ) async {
    // All three are required and none can be guessed: the entity decides
    // whose return the money lands on, and the bank decides which parser
    // reads the file.
    final FakeApiClient api = FakeApiClient(
      entities: <dynamic>[
        const Entity(id: 'e1', name: 'Owner', taxRegime: 'mtd_itsa'),
      ].cast(),
    );
    await pumpImports(tester, api);

    await tester.tap(find.text('Add statement'));
    await tester.pumpAndSettle();

    final FilledButton upload = tester.widget(
      find.widgetWithText(FilledButton, 'Upload'),
    );
    expect(upload.onPressed, isNull);
  });

  testWidgets('the bank list comes from the API, not a hard-coded list', (
    tester,
  ) async {
    // core/parser.py's registry is the source of truth. A copy in Dart
    // would drift into offering a bank the parser then refuses.
    final FakeApiClient api = FakeApiClient(
      banks: <String>['nationwide', 'mettle'],
    );
    await pumpImports(tester, api);

    await tester.tap(find.text('Add statement'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Choose').last);
    await tester.pumpAndSettle();

    expect(find.text('nationwide'), findsWidgets);
    expect(find.text('mettle'), findsWidgets);
  });
}
