/// The four places the app goes, in rail order.
///
/// One list, used by the router, the rail, and the tests. Two lists that
/// have to agree is how a nav item ends up highlighting the wrong screen.
library;

import 'package:flutter/material.dart';

@immutable
class Destination {
  const Destination({
    required this.path,
    required this.label,
    required this.icon,
    required this.selectedIcon,
  });

  final String path;
  final String label;
  final IconData icon;
  final IconData selectedIcon;
}

const List<Destination> kDestinations = <Destination>[
  Destination(
    path: '/',
    label: 'Dashboard',
    icon: Icons.dashboard_outlined,
    selectedIcon: Icons.dashboard,
  ),
  Destination(
    path: '/imports',
    label: 'Imports',
    icon: Icons.upload_file_outlined,
    selectedIcon: Icons.upload_file,
  ),
  Destination(
    path: '/review',
    label: 'Review',
    icon: Icons.rule_outlined,
    selectedIcon: Icons.rule,
  ),
  Destination(
    path: '/certificates',
    label: 'Certificates',
    icon: Icons.verified_outlined,
    selectedIcon: Icons.verified,
  ),
  Destination(
    path: '/portfolio',
    label: 'Portfolio',
    icon: Icons.home_work_outlined,
    selectedIcon: Icons.home_work,
  ),
];

/// Index of the destination whose path matches [location], or 0.
///
/// Longest-prefix, not equality: `/imports/abc` still lights up Imports.
/// `/` is excluded from prefix matching because everything starts with it.
int destinationIndexFor(String location) {
  int best = 0;
  int bestLength = 0;
  for (int i = 0; i < kDestinations.length; i++) {
    final String path = kDestinations[i].path;
    if (path == '/') continue;
    if (location == path || location.startsWith('$path/')) {
      if (path.length > bestLength) {
        best = i;
        bestLength = path.length;
      }
    }
  }
  return best;
}
