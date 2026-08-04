/// Material 3 theme construction for the landlord compliance app.
///
/// **The ColorScheme is hand-specified, not seed-generated.**
/// `ColorScheme.fromSeed` builds a whole tonal palette around one hue, which
/// is the right tool when you want a coherent family from a brand colour and
/// the wrong one here: this system deliberately pairs near-neutral warm greys
/// with a single high-chroma accent held under 10% of any surface. Seeding
/// from the gold would tint every surface gold, which is precisely the
/// "drenched" look `DESIGN.md` rules out. So the scheme carries the
/// `Palette` values across directly.
///
/// This replaced the `fromSeed(#0F5C57)` construction shipped in Task 4.
/// That deep teal was the first thing training data reaches for when you say
/// "UK tax compliance" — its own comment said so — and losing it was the
/// point.
///
/// :seealso: `DESIGN.md` for the palette's derivation and contrast figures;
///     `tokens.dart` for the values themselves.
library;

import 'package:flutter/material.dart';

import 'tokens.dart';

abstract final class AppTheme {
  /// Dark, from the scene rather than from taste: a home office in the
  /// evening, lamp on, dim room. Light exists and is properly built, but it
  /// is the secondary case.
  static const ThemeMode defaultThemeMode = ThemeMode.dark;

  static ThemeData light() => _build(Brightness.light);

  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final Palette palette = Palette.of(brightness);
    final ColorScheme colorScheme = _schemeFrom(palette, brightness);

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: colorScheme,
      scaffoldBackgroundColor: palette.bgBase,
      textTheme: _textTheme(palette),
      fontFamily: AppType.family,
      visualDensity: VisualDensity.standard,
      // Depth is a surface step plus a hairline, never a shadow: on a
      // near-black surface a drop shadow makes grey haze, not depth.
      cardTheme: CardThemeData(
        elevation: 0,
        color: palette.bgSurface,
        surfaceTintColor: Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: Radii.mdRadius,
          side: BorderSide(color: palette.rule),
        ),
        margin: EdgeInsets.zero,
      ),
      dividerTheme: DividerThemeData(
        color: palette.rule,
        thickness: 1,
        space: 1,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: palette.bgRaised,
        border: OutlineInputBorder(
          borderRadius: Radii.smRadius,
          borderSide: BorderSide(color: palette.ruleStrong),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: Radii.smRadius,
          borderSide: BorderSide(color: palette.ruleStrong),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: Radii.smRadius,
          borderSide: BorderSide(color: palette.accent, width: 2),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: palette.accent,
          foregroundColor: palette.accentInk,
          textStyle: AppType.label,
          minimumSize: const Size(0, 38),
          padding: const EdgeInsets.symmetric(
            horizontal: Spacing.md + 2,
            vertical: Spacing.sm + 2,
          ),
          shape: const RoundedRectangleBorder(borderRadius: Radii.smRadius),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          foregroundColor: palette.textBody,
          textStyle: AppType.label,
          shape: const RoundedRectangleBorder(borderRadius: Radii.smRadius),
        ),
      ),
      navigationRailTheme: NavigationRailThemeData(
        backgroundColor: palette.bgSurface,
        indicatorColor: palette.bgRaised,
        selectedIconTheme: IconThemeData(color: palette.accent, size: 20),
        unselectedIconTheme: IconThemeData(color: palette.textMuted, size: 20),
        selectedLabelTextStyle: AppType.label.copyWith(
          color: palette.textHigh,
          fontWeight: FontWeight.w600,
        ),
        unselectedLabelTextStyle: AppType.label.copyWith(
          color: palette.textMuted,
        ),
      ),
      pageTransitionsTheme: const PageTransitionsTheme(
        builders: <TargetPlatform, PageTransitionsBuilder>{
          TargetPlatform.android: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.iOS: CupertinoPageTransitionsBuilder(),
          TargetPlatform.macOS: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.windows: FadeForwardsPageTransitionsBuilder(),
          TargetPlatform.linux: FadeForwardsPageTransitionsBuilder(),
        },
      ),
    );
  }

  /// Map the palette onto M3's colour roles.
  ///
  /// `surfaceTint` is transparent throughout. M3 tints a surface with the
  /// primary colour as elevation rises; with a gold primary over warm greys
  /// that reads as a stain, and this system expresses depth through the
  /// three explicit surface steps instead.
  static ColorScheme _schemeFrom(Palette palette, Brightness brightness) {
    return ColorScheme(
      brightness: brightness,
      primary: palette.accent,
      onPrimary: palette.accentInk,
      primaryContainer: palette.accentDim,
      onPrimaryContainer: palette.accentInk,
      // Secondary is a neutral on purpose. A second chromatic role is how a
      // Restrained palette turns into a rainbow one screen at a time.
      secondary: palette.ruleStrong,
      onSecondary: palette.textHigh,
      secondaryContainer: palette.bgRaised,
      onSecondaryContainer: palette.textBody,
      surface: palette.bgBase,
      onSurface: palette.textBody,
      surfaceContainerLowest: palette.bgBase,
      surfaceContainerLow: palette.bgSurface,
      surfaceContainer: palette.bgSurface,
      surfaceContainerHigh: palette.bgRaised,
      surfaceContainerHighest: palette.bgRaised,
      onSurfaceVariant: palette.textMuted,
      outline: palette.ruleStrong,
      outlineVariant: palette.rule,
      error: palette.danger,
      onError: palette.accentInk,
      errorContainer: palette.dangerDim,
      onErrorContainer: palette.textHigh,
      surfaceTint: Colors.transparent,
    );
  }

  /// Map [AppType] onto M3's `TextTheme` slots.
  ///
  /// Colours are bound here rather than left to `Typography` because the
  /// palette, not Material's black/white defaults, decides them.
  static TextTheme _textTheme(Palette palette) {
    return TextTheme(
      displayLarge: AppType.display.copyWith(color: palette.textHigh),
      displayMedium: AppType.display.copyWith(color: palette.textHigh),
      displaySmall: AppType.display.copyWith(color: palette.textHigh),
      headlineLarge: AppType.titleLarge.copyWith(color: palette.textHigh),
      headlineMedium: AppType.titleLarge.copyWith(color: palette.textHigh),
      headlineSmall: AppType.title.copyWith(color: palette.textHigh),
      titleLarge: AppType.titleLarge.copyWith(color: palette.textHigh),
      titleMedium: AppType.title.copyWith(color: palette.textHigh),
      titleSmall: AppType.label.copyWith(color: palette.textHigh),
      bodyLarge: AppType.body.copyWith(color: palette.textBody),
      bodyMedium: AppType.body.copyWith(color: palette.textBody),
      bodySmall: AppType.meta.copyWith(color: palette.textMuted),
      labelLarge: AppType.label.copyWith(color: palette.textBody),
      labelMedium: AppType.label.copyWith(color: palette.textMuted),
      labelSmall: AppType.meta.copyWith(color: palette.textMuted),
    );
  }
}
