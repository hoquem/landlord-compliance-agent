/// Entities, properties, and who owns what share of which.
///
/// **The ownership editor is the money-critical part of this screen**, and
/// the only place in the app that mirrors a backend rule rather than simply
/// rendering a result. Per-entity export totals derive *exclusively* from
/// these percentages (HMRC PIM1035), and `0001_core.sql` deliberately does
/// not enforce "sums to 100" in the database — ownership is edited row by
/// row through transiently invalid totals, so a DB check would fire
/// mid-edit.
///
/// So the sum is checked live here **and** enforced by the API, and the
/// mirror is the point: the user should see the total go wrong as they type,
/// not discover it in a 422. The API remains the authority — this is a
/// courtesy, not a substitute, which is why a rejected save still shows the
/// backend's own message.
library;

import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../app/widgets/screen_scaffold.dart';
import '../../theme/tokens.dart';
import 'ownership_editor.dart';

class PortfolioScreen extends StatefulWidget {
  const PortfolioScreen({required this.api, super.key});

  final ApiClient api;

  @override
  State<PortfolioScreen> createState() => _PortfolioScreenState();
}

class _PortfolioScreenState extends State<PortfolioScreen> {
  List<Entity> _entities = <Entity>[];
  List<PropertyRef> _properties = <PropertyRef>[];
  String? _error;
  String? _editingOwnershipFor;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final List<Entity> entities = await widget.api.listEntities();
      final List<PropertyRef> properties = await widget.api.listProperties();
      if (!mounted) return;
      setState(() {
        _entities = entities;
        _properties = properties;
        _error = null;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    }
  }

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);
    final TextTheme text = Theme.of(context).textTheme;

    return ScreenScaffold(
      title: 'Portfolio',
      subtitle: 'The entities that own things, and the things they own.',
      child: ListView(
        padding: const EdgeInsets.symmetric(
          horizontal: Spacing.xl,
          vertical: Spacing.lg,
        ),
        children: <Widget>[
          if (_error != null)
            Text(
              '$_error.',
              style: text.bodyMedium?.copyWith(color: palette.danger),
            ),
          Text('Entities', style: text.titleMedium),
          const SizedBox(height: Spacing.sm),
          if (_entities.isEmpty)
            Text(
              'No entities yet. An entity is whoever the tax return is for.',
              style: text.bodyMedium?.copyWith(color: palette.textMuted),
            ),
          for (final Entity e in _entities)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: Spacing.xs),
              child: Row(
                children: <Widget>[
                  Expanded(child: Text(e.name, style: text.bodyLarge)),
                  Text(
                    e.taxRegime == 'mtd_itsa' ? 'MTD ITSA' : 'Corporation tax',
                    style: AppType.meta.copyWith(color: palette.textMuted),
                  ),
                ],
              ),
            ),
          const SizedBox(height: Spacing.xl),
          Text('Properties', style: text.titleMedium),
          const SizedBox(height: Spacing.sm),
          if (_properties.isEmpty)
            Text(
              'No properties yet.',
              style: text.bodyMedium?.copyWith(color: palette.textMuted),
            ),
          for (final PropertyRef p in _properties)
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: Spacing.xs),
                  child: Row(
                    children: <Widget>[
                      Expanded(child: Text(p.label, style: text.bodyLarge)),
                      TextButton(
                        onPressed: () => setState(
                          () => _editingOwnershipFor =
                              _editingOwnershipFor == p.id ? null : p.id,
                        ),
                        child: const Text('Ownership'),
                      ),
                    ],
                  ),
                ),
                if (_editingOwnershipFor == p.id)
                  OwnershipEditor(
                    api: widget.api,
                    propertyId: p.id,
                    entities: _entities,
                    onSaved: () => setState(() => _editingOwnershipFor = null),
                  ),
              ],
            ),
        ],
      ),
    );
  }
}
