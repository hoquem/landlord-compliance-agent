/// The frame every authenticated screen sits in: a nav rail and a pane.
///
/// No top bar. A second chrome band would cost a row of the review queue
/// and carry nothing the rail does not already say.
///
/// **Responsive behaviour is structural.** Under 900px the rail drops its
/// labels and keeps its icons; the type does not shrink. Fluid typography
/// in product UI makes a heading smaller in a narrow window, which is worse
/// rather than better.
library;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../theme/tokens.dart';
import 'destinations.dart';

/// Width below which the rail collapses to icons.
const double kRailCollapseWidth = 900;

class AppShell extends StatelessWidget {
  const AppShell({required this.child, required this.location, super.key});

  final Widget child;
  final String location;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final bool extended =
        MediaQuery.sizeOf(context).width >= kRailCollapseWidth;

    return Scaffold(
      body: Row(
        children: <Widget>[
          NavigationRail(
            extended: extended,
            minExtendedWidth: 220,
            selectedIndex: destinationIndexFor(location),
            onDestinationSelected: (int index) =>
                context.go(kDestinations[index].path),
            leading: _RailHeading(extended: extended),
            destinations: <NavigationRailDestination>[
              for (final Destination d in kDestinations)
                NavigationRailDestination(
                  icon: Icon(d.icon),
                  selectedIcon: Icon(d.selectedIcon),
                  label: Text(d.label),
                ),
            ],
          ),
          // A hairline, not a shadow or a border-heavy panel: depth in this
          // system is a surface step plus one rule.
          VerticalDivider(width: 1, thickness: 1, color: palette.rule),
          Expanded(child: child),
        ],
      ),
    );
  }
}

class _RailHeading extends StatelessWidget {
  const _RailHeading({required this.extended});

  final bool extended;

  @override
  Widget build(BuildContext context) {
    if (!extended) return const SizedBox(height: Spacing.lg);
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        Spacing.md,
        Spacing.lg,
        Spacing.md,
        Spacing.lg,
      ),
      child: Align(
        alignment: Alignment.centerLeft,
        child: Text(
          'Landlord Compliance',
          style: Theme.of(context).textTheme.titleSmall,
        ),
      ),
    );
  }
}
