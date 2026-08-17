/// Entities, properties, and who owns what share of which.
///
/// Redesigned as a property manager/investor dashboard:
///   - Summary header with key metrics
///   - Entity cards showing tax regime, property count
///   - Properties grouped by entity with ownership bars
///   - Compliance and mortgage info at a glance
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
    final mtdCount =
        realEntities.where((e) => e.taxRegime == 'mtd_itsa').length;
    final ctCount = realEntities.length - mtdCount;

    return ScreenScaffold(
      title: 'Portfolio',
      subtitle:
          '${_properties.length} properties · ${realEntities.length} entities',
      child: ListView(
        padding: const EdgeInsets.symmetric(
          horizontal: Spacing.lg,
          vertical: Spacing.sm,
        ),
        children: <Widget>[
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: Spacing.sm),
              child: SelectableText(
                '$_error.',
                style: AppType.body.copyWith(color: palette.danger),
              ),
            ),

          // ── Summary Row ──
          Row(
            children: <Widget>[
              _Stat(
                label: 'Properties',
                value: _properties.length,
                icon: Icons.home_work_outlined,
                palette: palette,
              ),
              const SizedBox(width: Spacing.sm),
              _Stat(
                label: 'Entities',
                value: realEntities.length,
                icon: Icons.business_outlined,
                palette: palette,
              ),
              const SizedBox(width: Spacing.sm),
              _Stat(
                label: 'MTD ITSA',
                value: mtdCount,
                icon: Icons.receipt_outlined,
                palette: palette,
              ),
              const SizedBox(width: Spacing.sm),
              _Stat(
                label: 'Corp Tax',
                value: ctCount,
                icon: Icons.account_balance_outlined,
                palette: palette,
              ),
            ],
          ),
          const SizedBox(height: Spacing.md),

          // ── Entity Cards with Properties ──
          for (final Entity e in realEntities)
            _EntityCard(
              entity: e,
              allProperties: _properties,
              allEntities: _entities,
              palette: palette,
              editingId: _editingOwnershipFor,
              onToggleOwnership: (id) => setState(
                () => _editingOwnershipFor =
                    _editingOwnershipFor == id ? null : id,
              ),
              api: widget.api,
              onSaved: () => setState(() => _editingOwnershipFor = null),
            ),
        ],
      ),
    );
  }
}

// ── Stat Pill ──────────────────────────────────────────────────
class _Stat extends StatelessWidget {
  const _Stat({
    required this.label,
    required this.value,
    required this.icon,
    required this.palette,
  });

  final String label;
  final int value;
  final IconData icon;
  final Palette palette;

  @override
  Widget build(BuildContext context) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(
          horizontal: Spacing.md,
          vertical: Spacing.sm,
        ),
        decoration: BoxDecoration(
          color: palette.bgSurface,
          borderRadius: Radii.smRadius,
          border: Border.all(color: palette.rule),
        ),
        child: Row(
          children: <Widget>[
            Icon(icon, size: 16, color: palette.accent),
            const SizedBox(width: Spacing.sm),
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  '$value',
                  style: AppType.title.copyWith(
                    color: palette.textHigh,
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                Text(
                  label,
                  style: AppType.meta.copyWith(color: palette.textMuted),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ── Entity Card with inline properties ───────────────────────
class _EntityCard extends StatelessWidget {
  const _EntityCard({
    required this.entity,
    required this.allProperties,
    required this.allEntities,
    required this.palette,
    required this.editingId,
    required this.onToggleOwnership,
    required this.api,
    required this.onSaved,
  });

  final Entity entity;
  final List<PropertyRef> allProperties;
  final List<Entity> allEntities;
  final Palette palette;
  final String? editingId;
  final void Function(String) onToggleOwnership;
  final ApiClient api;
  final VoidCallback onSaved;

  @override
  Widget build(BuildContext context) {
    final bool isMtd = entity.taxRegime == 'mtd_itsa';

    return Padding(
      padding: const EdgeInsets.only(bottom: Spacing.sm),
      child: Container(
        decoration: BoxDecoration(
          color: palette.bgSurface,
          borderRadius: Radii.smRadius,
          border: Border.all(color: palette.rule),
        ),
        child: ExpansionTile(
          tilePadding: const EdgeInsets.symmetric(
            horizontal: Spacing.md,
          ),
          dense: true,
          shape: const Border(),
          collapsedShape: const Border(),
          iconColor: palette.textMuted,
          collapsedIconColor: palette.textMuted,
          title: Row(
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
                    fontSize: 10,
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
              Text(
                '${allProperties.length}',
                style: AppType.meta.copyWith(color: palette.textMuted),
              ),
            ],
          ),
          children: <Widget>[
            for (final PropertyRef p in allProperties)
              _PropertyRow(
                property: p,
                palette: palette,
                isEditing: editingId == p.id,
                onToggleOwnership: () => onToggleOwnership(p.id),
                api: api,
                entities: allEntities,
                onSaved: onSaved,
              ),
          ],
        ),
      ),
    );
  }
}

// ── Property Row ──────────────────────────────────────────────
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

    final bool hasMortgage = property.mortgageType != 'none';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Divider(height: 1, thickness: 0.5, color: palette.rule),
        InkWell(
          onTap: onToggleOwnership,
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: Spacing.md,
              vertical: Spacing.sm,
            ),
            child: Row(
              children: <Widget>[
                // Mortgage indicator bar
                Container(
                  width: 3,
                  height: 28,
                  decoration: BoxDecoration(
                    color: hasMortgage ? palette.accentDim : palette.rule,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                const SizedBox(width: Spacing.sm),
                // Address + mortgage type
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
                          fontSize: 11,
                        ),
                      ),
                    ],
                  ),
                ),
                // Ownership icon
                Icon(
                  Icons.pie_chart_outline,
                  size: 16,
                  color: palette.textMuted,
                ),
              ],
            ),
          ),
        ),
        if (isEditing)
          Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: Spacing.md,
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