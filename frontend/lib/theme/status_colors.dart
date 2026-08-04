/// Colour for the five work states, as a [ThemeExtension].
///
/// An extension rather than bare `Color` constants in `tokens.dart`, because
/// a bare constant has no light/dark variant and would be wrong in one of
/// them (noted in Task 4's review, before either theme existed). Screens
/// read `Theme.of(context).extension<StatusColors>()`.
///
/// **Only two of the five carry chroma.** `needsYou` takes the accent,
/// `wrong` takes danger, and everything else recedes to a neutral. That is
/// the mechanism behind the core loop's reward: a review queue visibly
/// drains of colour as it is cleared, because settled work stops asking for
/// attention. Giving `settled` a green — the reflex — would make a finished
/// queue as loud as an unfinished one.
///
/// This supersedes the plan's "RAG status colours" for Task 22. RAG predates
/// `DESIGN.md`; three chromatic states on a screen that already carries
/// money would be the rainbow the five-state vocabulary exists to prevent.
library;

import 'package:flutter/material.dart';

import '../api/models.dart';
import 'tokens.dart';

@immutable
class StatusColors extends ThemeExtension<StatusColors> {
  const StatusColors({
    required this.working,
    required this.needsYou,
    required this.settled,
    required this.wrong,
    required this.setAside,
  });

  factory StatusColors.from(Palette palette) => StatusColors(
    working: palette.textMuted,
    needsYou: palette.accent,
    settled: palette.textMuted,
    wrong: palette.danger,
    setAside: palette.ruleStrong,
  );

  final Color working;
  final Color needsYou;
  final Color settled;
  final Color wrong;
  final Color setAside;

  Color of(WorkState state) => switch (state) {
    WorkState.working => working,
    WorkState.needsYou => needsYou,
    WorkState.settled => settled,
    WorkState.wrong => wrong,
    WorkState.setAside => setAside,
  };

  @override
  StatusColors copyWith({
    Color? working,
    Color? needsYou,
    Color? settled,
    Color? wrong,
    Color? setAside,
  }) => StatusColors(
    working: working ?? this.working,
    needsYou: needsYou ?? this.needsYou,
    settled: settled ?? this.settled,
    wrong: wrong ?? this.wrong,
    setAside: setAside ?? this.setAside,
  );

  @override
  StatusColors lerp(ThemeExtension<StatusColors>? other, double t) {
    if (other is! StatusColors) return this;
    return StatusColors(
      working: Color.lerp(working, other.working, t)!,
      needsYou: Color.lerp(needsYou, other.needsYou, t)!,
      settled: Color.lerp(settled, other.settled, t)!,
      wrong: Color.lerp(wrong, other.wrong, t)!,
      setAside: Color.lerp(setAside, other.setAside, t)!,
    );
  }
}
