/// Design tokens for the landlord compliance app.
///
/// Everything a screen would otherwise hard-code — colour, spacing, corner
/// radius, animation timing — lives here instead. Screens must consume these
/// tokens rather than inlining values, so a future rebrand (or a
/// productisation-era white-label pass) is a token swap, not a rewrite.
library;

import 'package:flutter/material.dart';

/// Seed colour for [ColorScheme.fromSeed].
///
/// Deep teal (`#0F5C57`): reads as dependable and financial/compliance-grade
/// rather than playful — steers Material 3's tonal palette generation away
/// from the default purple toward something a landlord would trust with
/// their HMRC paperwork.
const Color kSeedColor = Color(0xFF0F5C57);

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

/// Corner radii for surfaces, cards, and controls.
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
/// Per the design system's motion principles: purposeful and fast
/// (150-350ms), nothing decorative blocking input, and reduced-motion
/// preferences must be respected. Use [Motion.of] instead of these
/// constants directly wherever a [BuildContext] is available, so
/// `MediaQuery.disableAnimations` is honoured automatically.
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
  /// Returns [Duration.zero] when `MediaQuery.of(context).disableAnimations`
  /// is true, so callers never need to check that flag themselves. Defaults
  /// to [standard] when no duration is supplied. Use this wherever a widget
  /// picks a duration for an [AnimationController], implicit animation, or
  /// `flutter_animate` effect.
  static Duration of(BuildContext context, [Duration duration = standard]) {
    return MediaQuery.of(context).disableAnimations ? Duration.zero : duration;
  }
}
