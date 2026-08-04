/// Design tokens for the landlord compliance app.
///
/// Everything a screen would otherwise hard-code — colour, spacing, corner
/// radius, type, animation timing — lives here instead. Screens must consume
/// these tokens rather than inlining values.
///
/// The canonical source is `DESIGN.md` at the repo root, where the palette
/// is specified in OKLCH and its WCAG contrast ratios were computed rather
/// than eyeballed. The sRGB hex below is the round-trip of those values;
/// `test/theme_test.dart` re-derives the ratios from these Colors, so a
/// swatch nudged below AA fails a test instead of shipping.
library;

import 'package:flutter/material.dart';

/// One brightness's worth of the palette.
///
/// Two instances exist, [dark] and [light]. Dark is the default: the scene
/// in `PRODUCT.md` is a home office in the evening with the lamp on, and
/// that decides the theme rather than taste.
///
/// **The neutrals are warm.** Tinted toward amber (OKLCH hue 70/75) at
/// chroma 0.008–0.012, not the blue-grey every developer tool reaches for.
/// The difference between a study with a lamp on and a server room at 2am
/// is the whole aesthetic position, and it is one channel wide — pinned by
/// `test('neutrals are warm, never blue-grey')`, because a cool grey would
/// pass every contrast check while quietly undoing it.
@immutable
class Palette {
  const Palette({
    required this.bgBase,
    required this.bgSurface,
    required this.bgRaised,
    required this.rule,
    required this.ruleStrong,
    required this.textMuted,
    required this.textBody,
    required this.textHigh,
    required this.accent,
    required this.accentDim,
    required this.accentInk,
    required this.danger,
    required this.dangerDim,
  });

  /// Page background.
  final Color bgBase;

  /// Nav rail, panels, the selected review row.
  final Color bgSurface;

  /// Popovers, pills, inputs.
  final Color bgRaised;

  /// Hairline between rows. Depth in this system is a lightness step plus
  /// one of these, never a shadow.
  final Color rule;

  /// Section divisions and input borders.
  final Color ruleStrong;

  /// Metadata, settled status, the raw bank narrative.
  final Color textMuted;

  /// Body text.
  final Color textBody;

  /// The proposal line, amounts, headings.
  final Color textHigh;

  /// Needs-you, primary action, current selection. Held under 10% of any
  /// surface — the strategy is Restrained, and the screen is mostly money.
  final Color accent;

  /// Accent hover and accent-coloured rules.
  final Color accentDim;

  /// Text on an accent fill.
  final Color accentInk;

  /// Wrong: an expired certificate, a failed import. **Never an expense** —
  /// spending money on a roof is not an error.
  final Color danger;

  /// Danger borders and fills.
  final Color dangerDim;

  /// The eight neutrals, for the warmth check.
  List<Color> get neutrals => <Color>[
    bgBase,
    bgSurface,
    bgRaised,
    rule,
    ruleStrong,
    textMuted,
    textBody,
    textHigh,
  ];

  /// Every colour in this palette.
  List<Color> get all => <Color>[
    ...neutrals,
    accent,
    accentDim,
    accentInk,
    danger,
    dangerDim,
  ];

  /// The default. See the class docstring for why dark is not a preference.
  static const Palette dark = Palette(
    bgBase: Color(0xFF100D0A),
    bgSurface: Color(0xFF191512),
    bgRaised: Color(0xFF221E1A),
    rule: Color(0xFF34302B),
    ruleStrong: Color(0xFF4C4741),
    textMuted: Color(0xFF918B84),
    textBody: Color(0xFFD5D0CA),
    textHigh: Color(0xFFF4F1ED),
    accent: Color(0xFFEDB345),
    accentDim: Color(0xFFA97E2C),
    accentInk: Color(0xFF22190A),
    danger: Color(0xFFED695E),
    dangerDim: Color(0xFF8F3831),
  );

  /// Warm paper rather than white. The accent darkens considerably here:
  /// the dark theme's gold sits at 2.1:1 on paper and would be unreadable.
  static const Palette light = Palette(
    bgBase: Color(0xFFFAF6F1),
    bgSurface: Color(0xFFFFFDFA),
    bgRaised: Color(0xFFEFEAE4),
    rule: Color(0xFFDED8D1),
    ruleStrong: Color(0xFFC0B9B1),
    textMuted: Color(0xFF6E685F),
    textBody: Color(0xFF37322B),
    textHigh: Color(0xFF191510),
    accent: Color(0xFF9A6000),
    accentDim: Color(0xFFD4A96F),
    accentInk: Color(0xFFFEFBF8),
    danger: Color(0xFFB32322),
    dangerDim: Color(0xFFF2A89E),
  );

  /// The palette matching [brightness].
  static Palette of(Brightness brightness) =>
      brightness == Brightness.dark ? dark : light;
}

/// The type scale.
///
/// One family, fixed sizes (never fluid), ratio ≈1.15–1.25. Product UI has
/// more type elements than a marketing page, and exaggerated contrast
/// between them reads as noise rather than hierarchy.
abstract final class AppType {
  /// Inter, **bundled as an asset** rather than fetched by `google_fonts`.
  /// That package hits Google's CDN on first paint, which is an outbound
  /// request carrying the user's IP on a page displaying their bank
  /// transactions. This repo already disables CrewAI telemetry for exactly
  /// that reason; a font fetch is the same class of leak.
  ///
  /// **Necessary, not sufficient — measured 2026-08-04.** Bundling this font
  /// closed one hole in a wall that had three. CanvasKit is now self-hosted
  /// too (`web/flutter_bootstrap.js`); one request to `fonts.gstatic.com`
  /// for Flutter's Roboto fallback still remains. See DESIGN.md.
  static const String family = 'Inter';

  /// Digits that share a column, and a zero that can never be an O.
  ///
  /// The highest-value typographic decision in the product. Applied to
  /// [numeric], which every amount uses without exception.
  static const List<FontFeature> tabular = <FontFeature>[
    FontFeature.tabularFigures(),
    FontFeature.slashedZero(),
  ];

  /// Moments only: a cleared queue, a filed export. Nowhere else.
  static const TextStyle display = TextStyle(
    fontFamily: family,
    fontSize: 30,
    height: 1.15,
    fontWeight: FontWeight.w300,
    letterSpacing: -0.6,
  );

  /// Screen titles.
  static const TextStyle titleLarge = TextStyle(
    fontFamily: family,
    fontSize: 21,
    height: 1.25,
    fontWeight: FontWeight.w600,
    letterSpacing: -0.2,
  );

  /// Section headings, and the proposal line on a review row.
  static const TextStyle title = TextStyle(
    fontFamily: family,
    fontSize: 17,
    height: 1.35,
    fontWeight: FontWeight.w600,
  );

  /// Prose and form fields.
  static const TextStyle body = TextStyle(
    fontFamily: family,
    fontSize: 15,
    height: 1.5,
    fontWeight: FontWeight.w400,
  );

  /// Buttons, nav, column headers.
  static const TextStyle label = TextStyle(
    fontFamily: family,
    fontSize: 13,
    height: 1.4,
    fontWeight: FontWeight.w500,
    letterSpacing: 0.1,
  );

  /// Dates, status words, the raw bank narrative.
  static const TextStyle meta = TextStyle(
    fontFamily: family,
    fontSize: 12,
    height: 1.4,
    fontWeight: FontWeight.w400,
    letterSpacing: 0.1,
  );

  /// Every amount, everywhere.
  static const TextStyle numeric = TextStyle(
    fontFamily: family,
    fontSize: 15,
    height: 1.35,
    fontWeight: FontWeight.w500,
    fontFeatures: tabular,
  );
}

/// Spacing scale, in logical pixels, on a 4px base grid.
///
/// Use these instead of ad-hoc `SizedBox`/`EdgeInsets` values so density
/// stays consistent across screens.
abstract final class Spacing {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 16;
  static const double lg = 24;
  static const double xl = 32;
  static const double xxl = 48;
}

/// Corner radii for surfaces and controls.
abstract final class Radii {
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;

  static const BorderRadius smRadius = BorderRadius.all(Radius.circular(sm));
  static const BorderRadius mdRadius = BorderRadius.all(Radius.circular(md));
  static const BorderRadius lgRadius = BorderRadius.all(Radius.circular(lg));
  static const BorderRadius xlRadius = BorderRadius.all(Radius.circular(xl));
}

/// Motion durations and easing curves.
///
/// Purposeful and fast (150-350ms), nothing decorative blocking input, and
/// reduced-motion preferences honoured. Use [Motion.of] instead of these
/// constants directly wherever a [BuildContext] is available.
///
/// Motion marks a **state change**, never an arrival: there is no page-load
/// choreography, because the app loads into a task.
abstract final class Motion {
  /// Small, localized state changes (icon toggles, hover/press feedback).
  static const Duration fast = Duration(milliseconds: 150);

  /// Default transition duration: most enter/exit and navigation animations.
  static const Duration standard = Duration(milliseconds: 250);

  /// Large-surface or attention-drawing transitions (page transforms,
  /// full-screen reveals).
  static const Duration emphasized = Duration(milliseconds: 350);

  /// Standard M3 easing: most enter/exit transitions (pairs with [standard]).
  static const Curve standardCurve = Easing.standard;

  /// M3 easing for emphasized, larger-surface transitions (pairs with
  /// [emphasized]) — distinct from [standardCurve] per the M3 spec.
  static const Curve emphasizedCurve = Easing.emphasizedDecelerate;

  /// Easing for elements entering the screen.
  static const Curve enterCurve = Easing.standardDecelerate;

  /// Easing for elements leaving the screen.
  static const Curve exitCurve = Easing.standardAccelerate;

  /// Resolves [duration] against the current reduced-motion preference.
  ///
  /// Returns [Duration.zero] when `MediaQuery.disableAnimationsOf(context)`
  /// is true, so callers never need to check that flag themselves. Defaults
  /// to [standard] when no duration is supplied. Use this wherever a widget
  /// picks a duration for an [AnimationController], implicit animation, or
  /// `flutter_animate` effect.
  ///
  /// Uses the aspect-scoped `MediaQuery.disableAnimationsOf` rather than
  /// `MediaQuery.of(context).disableAnimations` so callers only rebuild when
  /// that specific flag changes, not on every MediaQuery change (window
  /// resize, keyboard inset, text-scale) — important on web where resize
  /// events are frequent.
  static Duration of(BuildContext context, [Duration duration = standard]) {
    return MediaQuery.disableAnimationsOf(context) ? Duration.zero : duration;
  }
}
