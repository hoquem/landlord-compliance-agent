/// A work state, shown as a word and a colour.
///
/// **Never a colour alone.** Every state the database has already carries a
/// name; showing it costs nothing and is the difference between an interface
/// that works for someone with colour vision deficiency and one that does
/// not. `PRODUCT.md` commits to this.
library;

import 'package:flutter/material.dart';

import '../../api/models.dart';
import '../../theme/status_colors.dart';
import '../../theme/tokens.dart';

class StatusPill extends StatelessWidget {
  const StatusPill({required this.state, required this.label, super.key});

  final WorkState state;
  final String label;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final StatusColors colors = Theme.of(context).extension<StatusColors>()!;

    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: Spacing.sm,
        vertical: Spacing.xs / 2,
      ),
      decoration: BoxDecoration(
        color: palette.bgRaised,
        borderRadius: Radii.smRadius,
      ),
      child: Text(
        label,
        style: AppType.meta.copyWith(color: colors.of(state)),
      ),
    );
  }
}
