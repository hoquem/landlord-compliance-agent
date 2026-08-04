/// Choosing a category, without leaving the queue.
///
/// **A popover, not a bottom sheet.** The plan asked for a sheet; `DESIGN.md`
/// bans the modal-as-first-thought and this is exactly why: correcting one
/// proposal must not cover the queue you are working through. Type to
/// filter, Enter takes the first match, Escape cancels.
///
/// The fifteen categories come from the API, not from a list in Dart —
/// `core/categories.py` is the source of truth and a copy here would be a
/// third opinion after the SQL enum.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../api/money.dart';
import '../../theme/tokens.dart';

/// Show the picker anchored to [context]'s widget. Returns the chosen
/// category id, or null if dismissed.
Future<String?> pickCategory(
  BuildContext context, {
  required List<String> categories,
  String? current,
}) {
  final RenderBox box = context.findRenderObject()! as RenderBox;
  final Offset topLeft = box.localToGlobal(Offset.zero);
  return showDialog<String>(
    context: context,
    // Barrier is transparent and dismissible: this reads as a popover
    // anchored to the row, not a dialog that takes the screen.
    barrierColor: Colors.transparent,
    builder: (BuildContext context) => _CategoryPopover(
      anchor: topLeft + Offset(0, box.size.height + Spacing.xs),
      categories: categories,
      current: current,
    ),
  );
}

class _CategoryPopover extends StatefulWidget {
  const _CategoryPopover({
    required this.anchor,
    required this.categories,
    this.current,
  });

  final Offset anchor;
  final List<String> categories;
  final String? current;

  @override
  State<_CategoryPopover> createState() => _CategoryPopoverState();
}

class _CategoryPopoverState extends State<_CategoryPopover> {
  String _filter = '';

  List<String> get _matches => widget.categories
      .where(
        (String c) =>
            categoryLabel(c).toLowerCase().contains(_filter.toLowerCase()),
      )
      .toList();

  void _choose(String? category) => Navigator.of(context).pop(category);

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final Size screen = MediaQuery.sizeOf(context);
    final double left = widget.anchor.dx.clamp(
      Spacing.md,
      (screen.width - 320 - Spacing.md).clamp(Spacing.md, double.infinity),
    );
    final double top = widget.anchor.dy.clamp(
      Spacing.md,
      (screen.height - 360 - Spacing.md).clamp(Spacing.md, double.infinity),
    );

    return Stack(
      children: <Widget>[
        Positioned(
          left: left,
          top: top,
          child: Material(
            color: palette.bgRaised,
            borderRadius: Radii.mdRadius,
            child: Container(
              width: 320,
              decoration: BoxDecoration(
                border: Border.all(color: palette.ruleStrong),
                borderRadius: Radii.mdRadius,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Padding(
                    padding: const EdgeInsets.all(Spacing.sm),
                    child: Shortcuts(
                      shortcuts: <ShortcutActivator, Intent>{
                        LogicalKeySet(LogicalKeyboardKey.escape):
                            const DismissIntent(),
                      },
                      child: TextField(
                        autofocus: true,
                        style: AppType.body.copyWith(color: palette.textBody),
                        decoration: const InputDecoration(
                          hintText: 'Filter categories',
                          isDense: true,
                        ),
                        onChanged: (String v) => setState(() => _filter = v),
                        onSubmitted: (_) {
                          if (_matches.isNotEmpty) _choose(_matches.first);
                        },
                      ),
                    ),
                  ),
                  Flexible(
                    child: ListView(
                      shrinkWrap: true,
                      children: <Widget>[
                        for (final String c in _matches)
                          _Option(
                            category: c,
                            selected: c == widget.current,
                            onTap: () => _choose(c),
                          ),
                        if (_matches.isEmpty)
                          Padding(
                            padding: const EdgeInsets.all(Spacing.md),
                            child: Text(
                              'No category matches "$_filter".',
                              style: AppType.meta.copyWith(
                                color: palette.textMuted,
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _Option extends StatelessWidget {
  const _Option({
    required this.category,
    required this.selected,
    required this.onTap,
  });

  final String category;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: Spacing.md,
          vertical: Spacing.sm,
        ),
        child: Text(
          categoryLabel(category),
          style: AppType.body.copyWith(
            color: selected ? palette.accent : palette.textBody,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }
}
