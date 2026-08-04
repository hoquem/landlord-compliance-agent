/// A screen that has a title and admits it has nothing else yet.
///
/// Deliberately not lorem, a spinner, or a plausible-looking fake table.
/// Tasks 20-22 replace these one at a time, and an honest placeholder makes
/// it obvious which ones are still empty when clicking around.
library;

import 'package:flutter/material.dart';

import '../theme/tokens.dart';

class PlaceholderScreen extends StatelessWidget {
  const PlaceholderScreen({required this.title, super.key});

  final String title;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    return Padding(
      padding: const EdgeInsets.all(Spacing.xl),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: Spacing.sm),
          Text(
            'Not built yet.',
            style: Theme.of(
              context,
            ).textTheme.bodyMedium?.copyWith(color: palette.textMuted),
          ),
        ],
      ),
    );
  }
}
