/// Editing one property's ownership set, with the sum checked as you type.
///
/// The rule mirrored here — shares sum to exactly 100 — is enforced by the
/// API and *not* by the database, on purpose: ownership is edited row by row
/// through transiently invalid totals, so a DB constraint would fire
/// mid-edit. `src/core/splits.py` is the authority, and confirming a
/// transaction against a property runs the real apportionment rather than
/// re-deriving the rule.
///
/// This mirror exists so the user watches the total go wrong as they type
/// instead of finding out in a 422. It is a courtesy, not a substitute:
/// a save the API rejects still shows the API's own message.
library;

import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../theme/tokens.dart';

class OwnershipEditor extends StatefulWidget {
  const OwnershipEditor({
    required this.api,
    required this.propertyId,
    required this.entities,
    required this.onSaved,
    super.key,
  });

  final ApiClient api;
  final String propertyId;
  final List<Entity> entities;
  final VoidCallback onSaved;

  @override
  State<OwnershipEditor> createState() => _OwnershipEditorState();
}

class _OwnershipEditorState extends State<OwnershipEditor> {
  /// entity id to percentage as typed. Absent means "no share".
  final Map<String, double> _shares = <String, double>{};

  /// One controller per entity, kept for the widget's lifetime.
  ///
  /// Building a controller inside `build` would recreate it on every
  /// keystroke and reset the cursor to the start -- which makes a
  /// percentage field almost unusable, and is invisible in a screenshot.
  final Map<String, TextEditingController> _controllers =
      <String, TextEditingController>{};
  bool _loaded = false;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final List<OwnershipShare> shares = await widget.api.getOwnership(
        widget.propertyId,
      );
      if (!mounted) return;
      setState(() {
        for (final OwnershipShare s in shares) {
          _shares[s.entityId] = s.percentage;
        }
        for (final Entity e in widget.entities) {
          _controllers[e.id] = TextEditingController(
            text: _shares[e.id]?.toStringAsFixed(2) ?? '',
          );
        }
        _loaded = true;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _loaded = true;
        _error = '$error';
      });
    }
  }

  double get _total =>
      _shares.values.fold(0, (double sum, double v) => sum + v);

  /// Compared at two decimal places, matching the column's precision.
  ///
  /// A bare `== 100` on doubles would refuse 33.33 + 33.33 + 33.34 for
  /// binary-representation reasons that have nothing to do with ownership.
  bool get _sumsTo100 => (_total * 100).round() == 10000;

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.setOwnership(widget.propertyId, <OwnershipShare>[
        for (final MapEntry<String, double> e in _shares.entries)
          if (e.value > 0)
            OwnershipShare(entityId: e.key, percentage: e.value),
      ]);
      if (!mounted) return;
      widget.onSaved();
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = '$error';
      });
    }
  }

  @override
  void dispose() {
    for (final TextEditingController c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;
    if (!_loaded) return const SizedBox(height: Spacing.xl);

    return Container(
      margin: const EdgeInsets.only(bottom: Spacing.md),
      padding: const EdgeInsets.all(Spacing.lg),
      decoration: BoxDecoration(
        color: palette.bgSurface,
        border: Border.all(color: palette.rule),
        borderRadius: Radii.mdRadius,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('Ownership', style: text.titleMedium),
          const SizedBox(height: Spacing.sm),
          Text(
            'Every penny of every amount is attributed by these shares, so '
            'they must total exactly 100%.',
            style: text.bodySmall,
          ),
          const SizedBox(height: Spacing.md),
          for (final Entity e in widget.entities)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: Spacing.xs),
              child: Row(
                children: <Widget>[
                  Expanded(child: Text(e.name, style: AppType.body.copyWith(color: palette.textBody))),
                  SizedBox(
                    width: 110,
                    child: TextField(
                      key: Key('share-${e.id}'),
                      controller: _controllers[e.id],
                      style: AppType.numeric.copyWith(color: palette.textBody),
                      textAlign: TextAlign.right,
                      decoration: const InputDecoration(
                        suffixText: '%',
                        isDense: true,
                      ),
                      onChanged: (String v) => setState(() {
                        final double? parsed = double.tryParse(v);
                        if (parsed == null) {
                          _shares.remove(e.id);
                        } else {
                          _shares[e.id] = parsed;
                        }
                      }),
                    ),
                  ),
                ],
              ),
            ),
          const SizedBox(height: Spacing.md),
          Row(
            children: <Widget>[
              Text('Total', style: AppType.label.copyWith(color: palette.textMuted)),
              const SizedBox(width: Spacing.sm),
              Text(
                '${_total.toStringAsFixed(2)}%',
                style: AppType.numeric.copyWith(
                  // The one place a number is coloured in this app: this is
                  // not money, it is a rule being satisfied or not.
                  color: _sumsTo100 ? palette.textHigh : palette.danger,
                ),
              ),
              if (!_sumsTo100) ...<Widget>[
                const SizedBox(width: Spacing.sm),
                Text(
                  'must be 100.00%',
                  style: AppType.meta.copyWith(color: palette.danger),
                ),
              ],
            ],
          ),
          if (_error != null) ...<Widget>[
            const SizedBox(height: Spacing.md),
            Text(
              '$_error.',
              style: text.bodyMedium?.copyWith(color: palette.danger),
            ),
          ],
          const SizedBox(height: Spacing.md),
          FilledButton(
            onPressed: _sumsTo100 && !_busy ? _save : null,
            child: Text(_busy ? 'Saving' : 'Save ownership'),
          ),
        ],
      ),
    );
  }
}
