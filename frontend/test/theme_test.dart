// Contract tests for the design system: palette, type, motion.
//
// These pin what every screen relies on. The palette values come from
// DESIGN.md, where they were computed in OKLCH and checked for WCAG
// contrast numerically rather than by eye; the tests below re-derive the
// contrast ratios from the actual Color values, so a "small tweak" to a
// swatch that drops it below AA fails here rather than in someone's eyes.
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:landlord_compliance/theme/app_theme.dart';
import 'package:landlord_compliance/theme/tokens.dart';

/// WCAG 2.1 relative luminance.
double _luminance(Color c) {
  double channel(double v) =>
      v <= 0.04045 ? v / 12.92 : math.pow((v + 0.055) / 1.055, 2.4).toDouble();
  return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
}

/// WCAG 2.1 contrast ratio between two opaque colours.
double _contrast(Color a, Color b) {
  final double la = _luminance(a);
  final double lb = _luminance(b);
  final double hi = math.max(la, lb);
  final double lo = math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

void main() {
  group('Palette', () {
    test('dark is the default brightness', () {
      // The scene decides this: a home office in the evening, lamp on, dim
      // room (PRODUCT.md). Dark is not a style preference here.
      expect(AppTheme.defaultThemeMode, ThemeMode.dark);
    });

    test('no pure black and no pure white anywhere', () {
      // Every neutral is tinted toward the brand hue. A #000 or #fff in the
      // palette means someone reached past the tokens.
      for (final Palette palette in <Palette>[Palette.dark, Palette.light]) {
        for (final Color c in palette.all) {
          expect(c, isNot(const Color(0xFF000000)));
          expect(c, isNot(const Color(0xFFFFFFFF)));
        }
      }
    });

    test('neutrals are warm, never blue-grey', () {
      // The whole aesthetic position: a study with a lamp on, not a server
      // room. Warm means red channel >= blue channel on every neutral. A
      // cool grey would pass every contrast test and quietly undo it.
      for (final Palette palette in <Palette>[Palette.dark, Palette.light]) {
        for (final Color c in palette.neutrals) {
          expect(
            (c.r * 255).round(),
            greaterThanOrEqualTo((c.b * 255).round()),
            reason: 'neutral $c is cool; neutrals must be warm-tinted',
          );
        }
      }
    });

    test('every text colour clears WCAG AA on its own background', () {
      for (final Palette palette in <Palette>[Palette.dark, Palette.light]) {
        for (final Color fg in <Color>[
          palette.textMuted,
          palette.textBody,
          palette.textHigh,
          palette.accent,
          palette.danger,
        ]) {
          expect(
            _contrast(fg, palette.bgBase),
            greaterThanOrEqualTo(4.5),
            reason: '$fg on ${palette.bgBase} fails AA',
          );
        }
      }
    });

    test('text on the accent fill clears WCAG AA', () {
      // The primary button is the most-pressed control in the product.
      for (final Palette palette in <Palette>[Palette.dark, Palette.light]) {
        expect(
          _contrast(palette.accentInk, palette.accent),
          greaterThanOrEqualTo(4.5),
        );
      }
    });
  });

  group('AppTheme', () {
    testWidgets('both brightnesses use Material 3', (tester) async {
      for (final ThemeData theme in <ThemeData>[
        AppTheme.light(),
        AppTheme.dark(),
      ]) {
        await tester.pumpWidget(
          MaterialApp(theme: theme, home: const Placeholder()),
        );
        final BuildContext context = tester.element(find.byType(Placeholder));
        expect(Theme.of(context).useMaterial3, isTrue);
      }
    });

    test('light and dark themes have distinct brightness', () {
      expect(AppTheme.light().brightness, Brightness.light);
      expect(AppTheme.dark().brightness, Brightness.dark);
    });

    test('the ColorScheme is hand-specified, not seed-generated', () {
      // ColorScheme.fromSeed builds a tonal palette around one hue, which
      // would tint every surface gold. This system pairs near-neutral warm
      // greys with a single high-chroma accent, so the scheme carries the
      // DESIGN.md values directly.
      expect(AppTheme.dark().colorScheme.surface, Palette.dark.bgBase);
      expect(AppTheme.dark().colorScheme.primary, Palette.dark.accent);
      expect(AppTheme.dark().colorScheme.onPrimary, Palette.dark.accentInk);
      expect(AppTheme.light().colorScheme.surface, Palette.light.bgBase);
      expect(AppTheme.light().colorScheme.primary, Palette.light.accent);
    });

    test('elevation never tints a surface', () {
      // Depth comes from the three surface steps plus a hairline rule.
      // M3's surfaceTint overlay would wash warm greys with gold as
      // elevation rises.
      expect(AppTheme.dark().colorScheme.surfaceTint, Colors.transparent);
      expect(AppTheme.light().colorScheme.surfaceTint, Colors.transparent);
    });

    test('every numeric style is tabular with a slashed zero', () {
      // The highest-value typographic decision in a ledger: amounts share a
      // digit column and a zero can never be read as an O.
      final List<FontFeature> features = AppType.numeric.fontFeatures!;
      expect(features, contains(const FontFeature.tabularFigures()));
      expect(features, contains(const FontFeature.slashedZero()));
    });

    test('the type scale uses the bundled family, not a runtime fetch', () {
      // google_fonts pulls from Google's CDN on first paint, carrying the
      // user's IP on a page showing their bank transactions.
      expect(AppType.body.fontFamily, AppType.family);
      expect(AppType.numeric.fontFamily, AppType.family);
      expect(AppType.display.fontFamily, AppType.family);
    });

    test('the type scale is fixed and ascending', () {
      final List<double> sizes = <double>[
        AppType.meta.fontSize!,
        AppType.label.fontSize!,
        AppType.body.fontSize!,
        AppType.title.fontSize!,
        AppType.titleLarge.fontSize!,
        AppType.display.fontSize!,
      ];
      for (int i = 1; i < sizes.length; i++) {
        expect(sizes[i], greaterThan(sizes[i - 1]));
      }
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

    testWidgets('Motion.of returns the requested duration by default', (
      tester,
    ) async {
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
    });

    testWidgets('Motion.of returns zero when animations are disabled', (
      tester,
    ) async {
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
    });

    testWidgets('Motion.of with no explicit duration defaults to standard', (
      tester,
    ) async {
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
    });

    testWidgets('Motion.of with no duration is zero when animations are off', (
      tester,
    ) async {
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
    });
  });
}
