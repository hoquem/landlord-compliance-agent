/// Entities, properties, and who owns what share of which.
///
/// Expandable bg-surface panels showing compliance-focused data:
///   - Entity (from ownership, not guessed)
///   - Mortgage type (editable)
///   - Ownership % (editable)
///   - Finance cost classification (residential/non-residential)
///   - Compliance certificates (Gas Safety, EICR, EPC) with status
///   - HMO licensing flag + bedroom count
///   - Bank accounts linked to the entity
library;

import 'package:flutter/material.dart';

import '../../api/api_client.dart';
import '../../api/models.dart';
import '../../app/widgets/screen_scaffold.dart';
import '../../app/widgets/status_pill.dart';
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
  List<PropertyCertificates> _certs = <PropertyCertificates>[];
  String? _error;
  String? _expandedPropertyId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final List<Entity> entities = await widget.api.listEntities();
      final List<PropertyRef> properties = await widget.api.listProperties();
      final List<PropertyCertificates> certs =
          await widget.api.listCertificates();
      if (!mounted) return;
      setState(() {
        _entities = entities;
        _properties = properties;
        _certs = certs;
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
          for (final PropertyRef p in _properties)
            _PropertyPanel(
              key: ValueKey(p.id),
              property: p,
              entities: _entities,
              certs: _certs
                  .where((c) => c.propertyId == p.id)
                  .fold<List<Certificate>>(<Certificate>[],
                      (acc, c) => acc..addAll(c.certificates)),
              api: widget.api,
              isExpanded: _expandedPropertyId == p.id,
              onToggle: () => setState(
                () => _expandedPropertyId =
                    _expandedPropertyId == p.id ? null : p.id,
              ),
              onSaved: _load,
              palette: palette,
            ),
          if (_properties.isEmpty && _entities.isEmpty && _error == null)
            Padding(
              padding: const EdgeInsets.only(top: Spacing.xl),
              child: Text(
                'No entities or properties yet.',
                style: AppType.body.copyWith(color: palette.textMuted),
              ),
            ),
        ],
      ),
    );
  }
}

// ── Property Panel ─────────────────────────────────────────────
class _PropertyPanel extends StatefulWidget {
  const _PropertyPanel({
    required this.property,
    required this.entities,
    required this.certs,
    required this.api,
    required this.isExpanded,
    required this.onToggle,
    required this.onSaved,
    required this.palette,
    super.key,
  });

  final PropertyRef property;
  final List<Entity> entities;
  final List<Certificate> certs;
  final ApiClient api;
  final bool isExpanded;
  final VoidCallback onToggle;
  final VoidCallback onSaved;
  final Palette palette;

  @override
  State<_PropertyPanel> createState() => _PropertyPanelState();
}

class _PropertyPanelState extends State<_PropertyPanel> {
  bool _editingMortgage = false;
  bool _editingOwnership = false;
  String? _selectedMortgageType;
  List<OwnershipShare>? _ownership;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (widget.isExpanded && _ownership == null) {
      _loadOwnership();
    }
  }

  Future<void> _loadOwnership() async {
    try {
      final shares = await widget.api.getOwnership(widget.property.id);
      if (mounted) setState(() => _ownership = shares);
    } catch (_) {}
  }

  String get _mortgageLabel => switch (widget.property.mortgageType) {
    'interest_only' => 'Interest Only',
    'repayment' => 'Repayment',
    _ => 'No Mortgage',
  };

  String get _ownershipLabel {
    if (_ownership == null) return '—';
    if (_ownership!.isEmpty) return '—';
    // Show primary owner name + percentage
    final real = _ownership!
        .where((s) => !widget.entities
            .any((e) => e.id == s.entityId && e.name.contains('Third Party')))
        .toList();
    if (real.isEmpty) return '${_ownership!.length} owners';
    final share = real.first;
    final entity =
        widget.entities.where((e) => e.id == share.entityId).firstOrNull;
    final name = entity?.name ?? 'Unknown';
    if (name.length > 20) {
      return '${share.percentage.toStringAsFixed(0)}% $name';
    }
    return '$name ${share.percentage.toStringAsFixed(0)}%';
  }

  String get _entityName {
    if (_ownership == null || _ownership!.isEmpty) return '—';
    final real = _ownership!
        .where((s) => !widget.entities
            .any((e) => e.id == s.entityId && e.name.contains('Third Party')))
        .toList();
    if (real.isEmpty) return '—';
    final entity =
        widget.entities.where((e) => e.id == real.first.entityId).firstOrNull;
    return entity?.name ?? '—';
  }

  String get _entityTaxRegime {
    if (_ownership == null || _ownership!.isEmpty) return '';
    final real = _ownership!
        .where((s) => !widget.entities
            .any((e) => e.id == s.entityId && e.name.contains('Third Party')))
        .toList();
    if (real.isEmpty) return '';
    final entity =
        widget.entities.where((e) => e.id == real.first.entityId).firstOrNull;
    return entity?.taxRegime ?? '';
  }

  String _certStatus(Certificate c) {
    final now = DateTime.now();
    if (c.expiryDate.isBefore(now)) return 'expired';
    if (c.expiryDate.difference(now).inDays < 60) return 'expiring';
    return 'valid';
  }

  String _certLabel(String type) => switch (type) {
    'gas_safety' => 'Gas Safety',
    'eicr' => 'EICR',
    'epc' => 'EPC',
    _ => type,
  };

  Future<void> _saveMortgageType() async {
    if (_selectedMortgageType == null) return;
    try {
      await widget.api.updateProperty(
        widget.property.id,
        mortgageType: _selectedMortgageType,
      );
      if (mounted) {
        setState(() => _editingMortgage = false);
        widget.onSaved();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to update: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final Palette palette = widget.palette;

    return Padding(
      padding: const EdgeInsets.only(bottom: Spacing.sm),
      child: Column(
        children: <Widget>[
          // Collapsed header
          InkWell(
            onTap: widget.onToggle,
            child: Container(
              color: palette.bgSurface,
              padding: const EdgeInsets.symmetric(
                horizontal: Spacing.md,
                vertical: Spacing.sm + Spacing.xs,
              ),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        SelectableText(
                          widget.property.label,
                          style: AppType.body.copyWith(
                            color: palette.textHigh,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                        Text(
                          _mortgageLabel,
                          style: AppType.meta.copyWith(
                            color: palette.textMuted,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Text(
                    _ownershipLabel,
                    style: AppType.numeric.copyWith(color: palette.textMuted),
                  ),
                  const SizedBox(width: Spacing.sm),
                  Icon(
                    widget.isExpanded
                        ? Icons.keyboard_arrow_up
                        : Icons.keyboard_arrow_down,
                    size: 18,
                    color: palette.textMuted,
                  ),
                ],
              ),
            ),
          ),

          // Expanded detail
          if (widget.isExpanded)
            Container(
              color: palette.bgSurface,
              padding: const EdgeInsets.fromLTRB(
                Spacing.md,
                0,
                Spacing.md,
                Spacing.md,
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  // Entity (from ownership)
                  _DetailRow(
                    label: 'Entity',
                    value: _entityName,
                    badge: _entityTaxRegime == 'mtd_itsa' ? 'MTD' : 'CT',
                    palette: palette,
                  ),

                  // Mortgage type (editable)
                  _DetailRow(
                    label: 'Mortgage',
                    value: _mortgageLabel,
                    palette: palette,
                    onEdit: () => setState(
                      () {
                        _editingMortgage = !_editingMortgage;
                        _selectedMortgageType = widget.property.mortgageType;
                      },
                    ),
                  ),
                  if (_editingMortgage) ...<Widget>[
                    const SizedBox(height: Spacing.xs),
                    _MortgageEditor(
                      currentType: _selectedMortgageType ??
                          widget.property.mortgageType,
                      onChanged: (v) =>
                          setState(() => _selectedMortgageType = v),
                      onSave: _saveMortgageType,
                      onCancel: () =>
                          setState(() => _editingMortgage = false),
                      palette: palette,
                    ),
                  ],

                  // Ownership (editable)
                  _DetailRow(
                    label: 'Ownership',
                    value: _ownershipLabel,
                    palette: palette,
                    onEdit: () => setState(
                      () => _editingOwnership = !_editingOwnership,
                    ),
                  ),
                  if (_editingOwnership)
                    Padding(
                      padding: const EdgeInsets.only(
                        left: Spacing.lg,
                        top: Spacing.xs,
                        bottom: Spacing.sm,
                      ),
                      child: OwnershipEditor(
                        api: widget.api,
                        propertyId: widget.property.id,
                        entities: widget.entities,
                        onSaved: () {
                          setState(() => _editingOwnership = false);
                          _loadOwnership();
                          widget.onSaved();
                        },
                      ),
                    ),

                  // Finance cost classification
                  _DetailRow(
                    label: 'Type',
                    value: widget.property.financeCostClassification ==
                            'residential'
                        ? 'Residential'
                        : 'Non-Residential',
                    palette: palette,
                  ),

                  // EPC
                  _DetailRow(
                    label: 'EPC',
                    value: widget.property.epcRating != null
                        ? '${widget.property.epcRating}'
                            '${widget.property.epcExpiry != null ? " · exp ${widget.property.epcExpiry!.day}/${widget.property.epcExpiry!.month}/${widget.property.epcExpiry!.year}" : ""}'
                        : 'Not recorded',
                    palette: palette,
                  ),

                  // HMO / Licensing
                  _DetailRow(
                    label: 'Licensing',
                    value: widget.property.licensingFlag
                        ? 'HMO licensed${widget.property.bedroomCount != null ? " · ${widget.property.bedroomCount} beds" : ""}'
                        : 'Not required${widget.property.bedroomCount != null ? " · ${widget.property.bedroomCount} beds" : ""}',
                    palette: palette,
                  ),

                  // Compliance certificates
                  if (widget.certs.isNotEmpty) ...<Widget>[
                    const SizedBox(height: Spacing.xs),
                    Text(
                      'Certificates',
                      style: AppType.meta.copyWith(color: palette.textMuted),
                    ),
                    const SizedBox(height: Spacing.xs),
                    for (final c in widget.certs)
                      _CertRow(
                        cert: c,
                        status: _certStatus(c),
                        label: _certLabel(c.certificateType),
                        palette: palette,
                      ),
                  ] else ...<Widget>[
                    const SizedBox(height: Spacing.xs),
                    Text(
                      'Certificates',
                      style: AppType.meta.copyWith(color: palette.textMuted),
                    ),
                    const SizedBox(height: Spacing.xs),
                    Text(
                      'None on file. Add gas safety, EICR and EPC in the Certificates tab.',
                      style: AppType.meta.copyWith(color: palette.textMuted),
                    ),
                  ],
                ],
              ),
            ),
        ],
      ),
    );
  }
}

// ── Certificate Row ────────────────────────────────────────────
class _CertRow extends StatelessWidget {
  const _CertRow({
    required this.cert,
    required this.status,
    required this.label,
    required this.palette,
  });

  final Certificate cert;
  final String status;
  final String label;
  final Palette palette;

  @override
  Widget build(BuildContext context) {
    final months = <String>[
      'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ];
    final expiry =
        '${cert.expiryDate.day} ${months[cert.expiryDate.month - 1]} ${cert.expiryDate.year}';

    final WorkState state = switch (status) {
      'expired' => WorkState.wrong,
      'expiring' => WorkState.needsYou,
      _ => WorkState.settled,
    };

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: <Widget>[
          SizedBox(
            width: 100,
            child: Text(label, style: AppType.body.copyWith(
              color: palette.textHigh,
            )),
          ),
          Expanded(
            child: Text(expiry, style: AppType.numeric.copyWith(
              color: palette.textMuted,
            )),
          ),
          SizedBox(
            width: 80,
            child: Align(
              alignment: Alignment.centerRight,
              child: StatusPill(
                state: state,
                label: switch (status) {
                  'expired' => 'Lapsed',
                  'expiring' => 'Expiring',
                  _ => 'Valid',
                },
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ── Detail Row ─────────────────────────────────────────────────
class _DetailRow extends StatelessWidget {
  const _DetailRow({
    required this.label,
    required this.value,
    required this.palette,
    this.badge,
    this.onEdit,
  });

  final String label;
  final String value;
  final Palette palette;
  final String? badge;
  final VoidCallback? onEdit;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: Spacing.xs + 2),
      child: Row(
        children: <Widget>[
          SizedBox(
            width: 120,
            child: Text(
              label,
              style: AppType.meta.copyWith(color: palette.textMuted),
            ),
          ),
          Expanded(
            child: SelectableText(
              value,
              style: AppType.body.copyWith(color: palette.textHigh),
            ),
          ),
          if (badge != null) ...<Widget>[
            Container(
              padding: const EdgeInsets.symmetric(
                horizontal: Spacing.sm,
                vertical: 2,
              ),
              decoration: BoxDecoration(
                color: badge == 'MTD' ? palette.accentDim : palette.bgRaised,
                borderRadius: Radii.smRadius,
              ),
              child: Text(
                badge!,
                style: AppType.meta.copyWith(
                  color: badge == 'MTD'
                      ? palette.accentInk
                      : palette.textMuted,
                  fontWeight: FontWeight.w600,
                  fontSize: 11,
                ),
              ),
            ),
            const SizedBox(width: Spacing.sm),
          ],
          if (onEdit != null)
            TextButton(
              onPressed: onEdit,
              style: TextButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: Spacing.sm),
                minimumSize: const Size(40, 28),
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
              child: Text(
                'Edit',
                style: AppType.label.copyWith(color: palette.accent),
              ),
            ),
        ],
      ),
    );
  }
}

// ── Mortgage Editor ────────────────────────────────────────────
class _MortgageEditor extends StatelessWidget {
  const _MortgageEditor({
    required this.currentType,
    required this.onChanged,
    required this.onSave,
    required this.onCancel,
    required this.palette,
  });

  final String currentType;
  final ValueChanged<String> onChanged;
  final VoidCallback onSave;
  final VoidCallback onCancel;
  final Palette palette;

  @override
  Widget build(BuildContext context) {
    const options = <String, String>{
      'none': 'No Mortgage',
      'interest_only': 'Interest Only',
      'repayment': 'Repayment (Interest + Capital)',
    };

    return Padding(
      padding: const EdgeInsets.only(left: Spacing.lg),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          for (final entry in options.entries)
            InkWell(
              onTap: () => onChanged(entry.key),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Row(
                  children: <Widget>[
                    Icon(
                      currentType == entry.key
                          ? Icons.radio_button_checked
                          : Icons.radio_button_unchecked,
                      size: 16,
                      color: currentType == entry.key
                          ? palette.accent
                          : palette.textMuted,
                    ),
                    const SizedBox(width: Spacing.sm),
                    Text(
                      entry.value,
                      style: AppType.body.copyWith(
                        color: currentType == entry.key
                            ? palette.textHigh
                            : palette.textMuted,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          const SizedBox(height: Spacing.sm),
          Row(
            children: <Widget>[
              FilledButton(
                onPressed: onSave,
                style: FilledButton.styleFrom(
                  minimumSize: const Size(60, 32),
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                child: const Text('Save'),
              ),
              const SizedBox(width: Spacing.sm),
              TextButton(
                onPressed: onCancel,
                child: const Text('Cancel'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

// ── Error ──────────────────────────────────────────────────────
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