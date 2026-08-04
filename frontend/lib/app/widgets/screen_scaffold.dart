/// The common frame for a content screen: title, optional action, body.
///
/// Exists so four screens do not each invent their own heading spacing.
/// Consistency is an affordance here, not a stylistic preference — a user
/// navigating between Imports and Review should not have to re-find where
/// the title lives.
library;

import 'package:flutter/material.dart';

import '../../theme/tokens.dart';

class ScreenScaffold extends StatelessWidget {
  const ScreenScaffold({
    required this.title,
    required this.child,
    this.subtitle,
    this.action,
    super.key,
  });

  final String title;
  final String? subtitle;
  final Widget? action;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(
            Spacing.xl,
            Spacing.xl,
            Spacing.xl,
            Spacing.lg,
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(title, style: text.titleLarge),
                    if (subtitle != null) ...<Widget>[
                      const SizedBox(height: Spacing.xs),
                      Text(
                        subtitle!,
                        style: text.bodyMedium?.copyWith(
                          color: palette.textMuted,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              if (action != null) action!,
            ],
          ),
        ),
        Divider(height: 1, thickness: 1, color: palette.rule),
        Expanded(child: child),
      ],
    );
  }
}
