// Failing-first tests for the M3 theme + motion token package.
//
// These tests pin down the public contract screens will rely on:
// - AppTheme.light()/dark() produce Material 3 ThemeData.
// - Motion durations are fixed, named constants (no magic numbers in screens).
// - Motion.of(context) collapses to Duration.zero when the platform/user has
//   requested reduced motion (MediaQuery.disableAnimations), per the design
//   system's accessibility requirement.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:landlord_compliance/theme/app_theme.dart';
import 'package:landlord_compliance/theme/tokens.dart';

void main() {
  group('AppTheme', () {
    testWidgets('light theme uses Material 3', (tester) async {
      await tester.pumpWidget(
        MaterialApp(theme: AppTheme.light(), home: const Placeholder()),
      );

      final BuildContext context = tester.element(find.byType(Placeholder));
      expect(Theme.of(context).useMaterial3, isTrue);
    });

    testWidgets('dark theme uses Material 3', (tester) async {
      await tester.pumpWidget(
        MaterialApp(theme: AppTheme.dark(), home: const Placeholder()),
      );

      final BuildContext context = tester.element(find.byType(Placeholder));
      expect(Theme.of(context).useMaterial3, isTrue);
    });

    test('light and dark themes have distinct brightness', () {
      expect(AppTheme.light().brightness, Brightness.light);
      expect(AppTheme.dark().brightness, Brightness.dark);
    });

    test('themes derive their ColorScheme from the shared seed colour', () {
      final ColorScheme expectedLight = ColorScheme.fromSeed(
        seedColor: kSeedColor,
        brightness: Brightness.light,
      );
      final ColorScheme expectedDark = ColorScheme.fromSeed(
        seedColor: kSeedColor,
        brightness: Brightness.dark,
      );

      expect(AppTheme.light().colorScheme.brightness, Brightness.light);
      expect(AppTheme.light().colorScheme.primary, expectedLight.primary);
      expect(AppTheme.dark().colorScheme.brightness, Brightness.dark);
      expect(AppTheme.dark().colorScheme.primary, expectedDark.primary);
    });

    test('card theme uses the token radius, not a default shape', () {
      expect(
        AppTheme.light().cardTheme.shape,
        const RoundedRectangleBorder(borderRadius: Radii.mdRadius),
      );
    });
  });

  group('Motion tokens', () {
    test('fast is 150ms', () {
      expect(Motion.fast, const Duration(milliseconds: 150));
    });

    test('standard is 250ms', () {
      expect(Motion.standard, const Duration(milliseconds: 250));
    });

    test('emphasized is 350ms', () {
      expect(Motion.emphasized, const Duration(milliseconds: 350));
    });

    testWidgets(
      'Motion.of(context) returns the requested duration by default',
      (tester) async {
        late Duration resolved;
        await tester.pumpWidget(
          Builder(
            builder: (context) {
              resolved = Motion.of(context, Motion.standard);
              return const SizedBox.shrink();
            },
          ),
        );

        expect(resolved, Motion.standard);
      },
    );

    testWidgets(
      'Motion.of(context) returns Duration.zero when animations are disabled',
      (tester) async {
        late Duration resolved;
        await tester.pumpWidget(
          MediaQuery(
            data: const MediaQueryData(disableAnimations: true),
            child: Builder(
              builder: (context) {
                resolved = Motion.of(context, Motion.emphasized);
                return const SizedBox.shrink();
              },
            ),
          ),
        );

        expect(resolved, Duration.zero);
      },
    );

    testWidgets(
      'Motion.of(context) with no explicit duration defaults to standard',
      (tester) async {
        late Duration resolved;
        await tester.pumpWidget(
          Builder(
            builder: (context) {
              resolved = Motion.of(context);
              return const SizedBox.shrink();
            },
          ),
        );

        expect(resolved, Motion.standard);
      },
    );

    testWidgets(
      'Motion.of(context) with no explicit duration is zero when animations are disabled',
      (tester) async {
        late Duration resolved;
        await tester.pumpWidget(
          MediaQuery(
            data: const MediaQueryData(disableAnimations: true),
            child: Builder(
              builder: (context) {
                resolved = Motion.of(context);
                return const SizedBox.shrink();
              },
            ),
          ),
        );

        expect(resolved, Duration.zero);
      },
    );
  });
}
