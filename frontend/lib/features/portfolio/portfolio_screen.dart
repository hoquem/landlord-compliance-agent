/// Entities, properties, and who owns what share of which.
///
/// Redesigned to follow the calm, row-based design language:
///   - No cards, no hero metrics
///   - Entities as a simple list with tax regime badges
///   - Properties as hairline-divided rows (matching review/imports)
///   - Ownership editor inline
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

    final realEntities =
        _entities.where((e) => !e.name.contains('Third Party')).toList();

    return ScreenScaffold(
      title: 'Portfolio',
      subtitle: _properties.isEmpty && _entities.isEmpty
          ? null
          : '${_properties.length} properties · ${realEntities.length} entities',
      child: ListView(
        padding: const EdgeInsets.symmetric(
          horizontal: Spacing.xl,
          vertical: Spacing.lg,
        ),
        children: <Widget>[
          if (_error != null)
            _ErrorLine(message: _error!, onRetry: _load),

          // ── Entities ──
          if (realEntities.isNotEmpty) ...<Widget>[
            Text('Entities', style: AppType.title.copyWith(
              color: palette.textMuted,
              fontWeight: FontWeight.w600,
            )),
            const SizedBox(height: Spacing.sm),
            for (final Entity e in realEntities)
              _EntityRow(entity: e, palette: palette),
            const SizedBox(height: Spacing.xl),
          ],

          // ── Properties ──
          if (_properties.isNotEmpty) ...<Widget>[
            Text('Properties', style: AppType.title.copyWith(
              color: palette.textMuted,
              fontWeight: FontWeight.w600,
            )),
            const SizedBox(height: Spacing.sm),
            for (final PropertyRef p in _properties)
              _PropertyRow(
                property: p,
                palette: palette,
                isEditing: _editingOwnershipFor == p.id,
                onToggleOwnership: () => setState(
                  () => _editingOwnershipFor =
                      _editingOwnershipFor == p.id ? null : p.id,
                ),
                api: widget.api,
                entities: _entities,
                onSaved: () => setState(() => _editingOwnershipFor = null),
              ),
          ],

          if (_properties.isEmpty && _entities.isEmpty && _error == null)
            Padding(
              padding: const EdgeInsets.only(top: Spacing.xl),
              child: Text(
                'No entities or properties yet. Add them through the API '
                'or seed the database.',
                style: AppType.body.copyWith(color: palette.textMuted),
              ),
            ),
        ],
      ),
    );
  }
}

// ── Entity Row (hairline-divided, no cards) ───────────────────
class _EntityRow extends StatelessWidget {
  const _EntityRow({required this.entity, required this.palette});

  final Entity entity;
  final Palette palette;

  @override
  Widget build(BuildContext context) {
    final bool isMtd = entity.taxRegime == 'mtd_itsa';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Divider(height: 1, thickness: 0.5, color: palette.rule),
        Padding(
          padding: const EdgeInsets.symmetric(
            horizontal: Spacing.sm,
            vertical: Spacing.sm,
          ),
          child: Row(
            children: <Widget>[
              Container(
                padding: const EdgeInsets.symmetric(
                  horizontal: Spacing.sm,
                  vertical: 2,
                ),
                decoration: BoxDecoration(
                  color: isMtd ? palette.accentDim : palette.bgRaised,
                  borderRadius: Radii.smRadius,
                ),
                child: Text(
                  isMtd ? 'MTD' : 'CT',
                  style: AppType.meta.copyWith(
                    color: isMtd ? palette.accentInk : palette.textMuted,
                    fontWeight: FontWeight.w600,
                    fontSize: 11,
                  ),
                ),
              ),
              const SizedBox(width: Spacing.sm),
              Expanded(
                child: SelectableText(
                  entity.name,
                  style: AppType.body.copyWith(
                    color: palette.textHigh,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// ── Property Row (hairline-divided, no cards) ─────────────────
class _PropertyRow extends StatelessWidget {
  const _PropertyRow({
    required this.property,
    required this.palette,
    required this.isEditing,
    required this.onToggleOwnership,
    required this.api,
    required this.entities,
    required this.onSaved,
  });

  final PropertyRef property;
  final Palette palette;
  final bool isEditing;
  final VoidCallback onToggleOwnership;
  final ApiClient api;
  final List<Entity> entities;
  final VoidCallback onSaved;

  @override
  Widget build(BuildContext context) {
    final String mortgageLabel = switch (property.mortgageType) {
      'interest_only' => 'Interest Only',
      'repayment' => 'Repayment',
      _ => 'No Mortgage',
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Divider(height: 1, thickness: 0.5, color: palette.rule),
        InkWell(
          onTap: onToggleOwnership,
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: Spacing.sm,
              vertical: Spacing.sm,
            ),
            child: Row(
              children: <Widget>[
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      SelectableText(
                        property.label,
                        style: AppType.body.copyWith(
                          color: palette.textHigh,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                      Text(
                        mortgageLabel,
                        style: AppType.meta.copyWith(
                          color: palette.textMuted,
                        ),
                      ),
                    ],
                  ),
                ),
                Text(
                  'Ownership',
                  style: AppType.label.copyWith(color: palette.accent),
                ),
                const SizedBox(width: Spacing.xs),
                Icon(
                  Icons.chevron_right,
                  size: 18,
                  color: palette.textMuted,
                ),
              ],
            ),
          ),
        ),
        if (isEditing)
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: Spacing.sm,
              vertical: Spacing.xs,
            ),
            child: OwnershipEditor(
              api: api,
              propertyId: property.id,
              entities: entities,
              onSaved: onSaved,
            ),
          ),
      ],
    );
  }
}

// ── Error line with retry ──────────────────────────────────────
class _ErrorLine extends StatelessWidget {
  const _ErrorLine({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final Palette palette = Palette.of(Theme.of(context).brightness);

    return Padding(
      padding: const EdgeInsets.only(bottom: Spacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SelectableText(
            'Something went wrong loading the portfolio.',
            style: AppType.title.copyWith(color: palette.danger),
          ),
          const SizedBox(height: Spacing.xs),
          SelectableText(
            message,
            style: AppType.body.copyWith(color: palette.textMuted),
          ),
          const SizedBox(height: Spacing.sm),
          TextButton(
            onPressed: onRetry,
            child: const Text('Try again'),
          ),
        ],
      ),
    );
  }
}